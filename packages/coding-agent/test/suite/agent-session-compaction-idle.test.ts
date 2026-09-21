import { fauxAssistantMessage } from "@earendil-works/pi-ai";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createHarness, getAssistantTexts, type Harness } from "./harness.js";

const harnesses: Harness[] = [];

afterEach(() => {
	vi.useRealTimers();
	vi.restoreAllMocks();
	while (harnesses.length > 0) harnesses.pop()?.cleanup();
});

async function createOverflowHarness(): Promise<Harness> {
	vi.useFakeTimers();
	const harness = await createHarness({
		settings: { compaction: { enabled: true, keepRecentTokens: 1 } },
		extensionFactories: [
			(pi) => {
				pi.on("session_before_compact", async (event) => ({
					compaction: {
						summary: "Retain the original request and continue it.",
						firstKeptEntryId: event.preparation.firstKeptEntryId,
						tokensBefore: event.preparation.tokensBefore,
					},
				}));
			},
		],
	});
	harnesses.push(harness);
	harness.setResponses([
		fauxAssistantMessage("seed response"),
		fauxAssistantMessage("", { stopReason: "error", errorMessage: "prompt is too long" }),
	]);
	await harness.session.prompt("seed context");
	await harness.session.prompt("complete the original request");
	expect(harness.eventsOfType("compaction_end")).toEqual([
		expect.objectContaining({ reason: "overflow", aborted: false, willRetry: true, result: expect.any(Object) }),
	]);
	return harness;
}

describe("post-compaction idle ownership", () => {
	it("waits across the timer, rescheduling, and the real continuation turn", async () => {
		const harness = await createOverflowHarness();
		let release = () => {};
		const gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		const continued = vi.fn();
		harness.appendResponses([
			async () => {
				continued();
				await gate;
				return fauxAssistantMessage("recovered answer");
			},
		]);
		const pause = harness.session.acquireQueuedWorkPause();
		let idle = false;
		const completion = harness.session.waitForIdle().then(() => {
			idle = true;
		});
		await vi.advanceTimersByTimeAsync(0);
		expect(idle).toBe(false);
		await vi.advanceTimersByTimeAsync(200);
		expect(continued).not.toHaveBeenCalled();
		expect(idle).toBe(false);
		pause.release();
		await vi.advanceTimersByTimeAsync(100);
		expect(continued).toHaveBeenCalledOnce();
		expect(idle).toBe(false);
		release();
		await completion;
		expect(getAssistantTexts(harness).at(-1)).toBe("recovered answer");
		expect(harness.eventsOfType("agent_end")).toHaveLength(3);
	});

	it.each([false, true])("settles abort with a running continuation: %s", async (startContinuation) => {
		const harness = await createOverflowHarness();
		const continued = vi.fn();
		harness.appendResponses([
			async (_context, options) => {
				continued();
				await new Promise<void>((resolve) => {
					if (options?.signal?.aborted) resolve();
					else options?.signal?.addEventListener("abort", () => resolve(), { once: true });
				});
				return fauxAssistantMessage("", { stopReason: "aborted" });
			},
		]);
		let idle = false;
		const completion = harness.session.waitForIdle().then(() => {
			idle = true;
		});
		await vi.advanceTimersByTimeAsync(startContinuation ? 100 : 0);
		expect(idle).toBe(false);
		expect(continued).toHaveBeenCalledTimes(startContinuation ? 1 : 0);
		await harness.session.abort();
		await completion;
		await vi.advanceTimersByTimeAsync(1000);
		expect(continued).toHaveBeenCalledTimes(startContinuation ? 1 : 0);
		expect(harness.session.isStreaming).toBe(false);
	});

	it("invalidates a cancelled callback waiting for refinement before a replacement starts", async () => {
		const harness = await createOverflowHarness();
		const internals = harness.session as unknown as {
			_waitForRefineIdle(): Promise<void>;
			_schedulePostCompactionContinue(): void;
		};
		let release = () => {};
		const gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		vi.spyOn(internals, "_waitForRefineIdle").mockImplementationOnce(() => gate);
		await vi.advanceTimersByTimeAsync(100);
		await harness.session.abort();
		await harness.session.waitForIdle();
		harness.appendResponses([fauxAssistantMessage("replacement answer")]);
		internals._schedulePostCompactionContinue();
		release();
		await vi.advanceTimersByTimeAsync(0);
		expect(harness.getPendingResponseCount()).toBe(1);
		await vi.advanceTimersByTimeAsync(100);
		await harness.session.waitForIdle();
		expect(getAssistantTexts(harness).at(-1)).toBe("replacement answer");
		expect(harness.eventsOfType("agent_end")).toHaveLength(3);
	});
});
