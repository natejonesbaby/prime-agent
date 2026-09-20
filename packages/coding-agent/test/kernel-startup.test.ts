import { createHmac } from "node:crypto";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Publisher, Router } from "zeromq";
import { KernelManager } from "../src/core/kernel/index.js";

let tempDir = "";

function writeExecutable(filePath: string, content: string): void {
	writeFileSync(filePath, content);
	chmodSync(filePath, 0o755);
}

interface ProbeMessage {
	header: { msg_id: string; msg_type: string };
	content: { code?: string };
}

async function simulatedKernel(options: { idleFirst?: boolean; neverReady?: boolean; idleDelayMs?: number } = {}) {
	const shell = new Router();
	const iopub = new Publisher();
	await shell.bind("tcp://127.0.0.1:*");
	await iopub.bind("tcp://127.0.0.1:*");
	const python = join(tempDir, "python");
	if (!shell.lastEndpoint || !iopub.lastEndpoint) throw new Error("Fixture sockets did not bind");
	const shellPort = Number(shell.lastEndpoint.split(":").at(-1));
	const iopubPort = Number(iopub.lastEndpoint.split(":").at(-1));
	writeExecutable(
		python,
		`#!${process.execPath}
const fs = require("node:fs");
const file = process.argv[process.argv.indexOf("-f") + 1];
const connection = JSON.parse(fs.readFileSync(file, "utf8"));
Object.assign(connection, {shell_port: ${shellPort}, iopub_port: ${iopubPort},
  stdin_port: ${shellPort}, control_port: ${shellPort}, hb_port: ${shellPort}});
fs.writeFileSync(file, JSON.stringify(connection));
setInterval(() => {}, 1000);
`,
	);
	let probes = 0;
	let executions = 0;
	let idlePublished = false;
	let executedBeforeReady = false;
	let closed = false;
	const delayedPublishes: Promise<void>[] = [];
	let firstProbe!: () => void;
	const observedProbe = new Promise<void>((resolve) => {
		firstProbe = resolve;
	});
	const manager = new KernelManager({ python, cwd: tempDir });
	function frames(
		parent: ProbeMessage,
		type: string,
		content: Record<string, unknown>,
		key: string,
		parentId = parent.header.msg_id,
	) {
		const parts = [
			Buffer.from(JSON.stringify({ msg_id: `${parentId}-${type}`, msg_type: type })),
			Buffer.from(JSON.stringify({ msg_id: parentId })),
			Buffer.from("{}"),
			Buffer.from(JSON.stringify(content)),
		];
		const hmac = createHmac("sha256", key);
		for (const part of parts) hmac.update(part);
		return [Buffer.from("<IDS|MSG>"), Buffer.from(hmac.digest("hex")), ...parts];
	}
	const serving = (async () => {
		try {
			for await (const request of shell) {
				const delimiter = request.findIndex((frame) => frame.toString() === "<IDS|MSG>");
				const message = {
					header: JSON.parse(request[delimiter + 2].toString()),
					content: JSON.parse(request[delimiter + 5].toString()),
				} as ProbeMessage;
				const route = request.slice(0, delimiter);
				const key = "fixture-key";
				if (message.header.msg_type === "kernel_info_request") {
					probes++;
					firstProbe();
					const publishIdle = async () => {
						if (options.neverReady) {
							await iopub.send(frames(message, "status", { execution_state: "idle" }, key, "unrelated-request"));
						} else if (probes >= 4) {
							idlePublished = true;
							await iopub.send(frames(message, "status", { execution_state: "idle" }, key));
						}
					};
					if (options.idleFirst) await publishIdle();
					await shell.send([...route, ...frames(message, "kernel_info_reply", {}, key)]);
					if (!options.idleFirst) {
						if (options.idleDelayMs) {
							delayedPublishes.push(
								sleep(options.idleDelayMs).then(async () => {
									if (!closed) await publishIdle();
								}),
							);
						} else await publishIdle();
					}
				} else if (message.header.msg_type === "execute_request") {
					executions++;
					executedBeforeReady ||= !idlePublished;
					if (!idlePublished) continue;
					await iopub.send(frames(message, "stream", { name: "stdout", text: "first-cell\n" }, key));
					await iopub.send(frames(message, "status", { execution_state: "idle" }, key));
				}
			}
		} catch (error) {
			if (!closed) throw error;
		}
	})();
	return {
		manager,
		observedProbe,
		counts: () => ({ probes, executions, executedBeforeReady }),
		close: async () => {
			closed = true;
			await manager.kill();
			shell.close();
			iopub.close();
			await serving;
			await Promise.all(delayedPublishes);
		},
	};
}

describe("KernelManager startup", () => {
	beforeEach(() => {
		tempDir = mkdtempSync(join(tmpdir(), "prime-agent-kernel-startup-"));
		vi.stubEnv("PRIME_AGENT_KERNEL_FORKSERVER", "0");
	});

	afterEach(() => {
		vi.unstubAllEnvs();
		if (tempDir) {
			rmSync(tempDir, { recursive: true, force: true });
			tempDir = "";
		}
	});

	it("surfaces kernels that exit before resolving ports", async () => {
		const python = join(tempDir, "python");
		writeExecutable(python, ["#!/bin/sh", 'echo "fake kernel died before binding" >&2', "exit 42", ""].join("\n"));
		const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
		const manager = new KernelManager({ python, cwd: tempDir });

		try {
			await expect(manager.execute("print(1)")).rejects.toThrow(
				/Kernel exited before resolving ports[\s\S]*fake kernel died before binding/,
			);
		} finally {
			errorSpy.mockRestore();
			await manager.dispose();
		}
	});

	it.each([false, true])(
		"waits for correlated IOPub readiness before the first cell (idle first: %s)",
		async (idleFirst) => {
			const fixture = await simulatedKernel({ idleFirst });
			const controller = new AbortController();
			const timer = setTimeout(() => controller.abort(), 2000);
			try {
				const result = await fixture.manager.execute("print('first-cell')", { signal: controller.signal });
				expect(result.status).toBe("ok");
				expect(result.stdout).toBe("first-cell\n");
				expect(fixture.counts().probes).toBeGreaterThanOrEqual(4);
				expect(fixture.counts()).toMatchObject({ executions: 1, executedBeforeReady: false });
			} finally {
				clearTimeout(timer);
				await fixture.close();
			}
		},
	);

	it("fails boundedly when shell replies arrive but only unrelated IOPub idle is observed", async () => {
		const fixture = await simulatedKernel({ neverReady: true });
		try {
			await expect(fixture.manager.execute("print('must-not-run')")).rejects.toThrow(
				/shell and IOPub did not become ready/,
			);
			expect(fixture.counts().executions).toBe(0);
			expect(fixture.manager.isRunning).toBe(false);
		} finally {
			await fixture.close();
		}
	});

	it("accepts a correlated idle arriving after a later readiness probe was sent", async () => {
		const fixture = await simulatedKernel({ idleDelayMs: 250 });
		try {
			await fixture.manager.start();
			expect(fixture.manager.isRunning).toBe(true);
			expect(fixture.counts().probes).toBeGreaterThanOrEqual(4);
			expect(fixture.counts().executions).toBe(0);
		} finally {
			await fixture.close();
		}
	});

	it("cancels startup while waiting for IOPub without executing a cell", async () => {
		const fixture = await simulatedKernel({ neverReady: true });
		const controller = new AbortController();
		const started = fixture.manager.start({ signal: controller.signal });
		const rejected = expect(started).rejects.toThrow("Kernel startup aborted");
		try {
			await fixture.observedProbe;
			controller.abort();
			await rejected;
			expect(fixture.counts().executions).toBe(0);
		} finally {
			await fixture.close();
		}
		expect(fixture.manager.isRunning).toBe(false);
	});
});
