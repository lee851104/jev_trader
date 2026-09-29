"use strict";

// One bounded replay, with no retries of decisions or settlements.
function createReplay({getState, execute, onUpdate, wait = () => new Promise(resolve => setTimeout(resolve, 900))}) {
  let running = false;
  let stopping = false;
  let progress;
  function emit(patch) {
    progress = {...progress, ...patch, running, stopping};
    onUpdate(progress);
  }
  return {
    isRunning: () => running,
    stop() {
      if (!running) return;
      stopping = true;
      emit({});
    },
    async start() {
      if (running) return;
      running = true;
      stopping = false;
      progress = {completed: 0, target: 5, stage: "input", outcome: null, error: null};
      const runId = getState()?.run_id;
      function state() {
        const value = getState();
        if (!runId || value?.run_id !== runId) throw new Error("模擬已變更，請重新開始自動執行。");
        if (!["ready", "pending", "finished"].includes(value.status)) throw new Error(value.error || "目前狀態無法繼續執行。");
        return value;
      }
      function checkResult(result, allowed) {
        if (result?.run_id !== runId || !allowed.includes(result.status)) throw new Error(result?.error || "操作未完成，自動執行已停止。");
      }
      emit({});
      try {
        while (progress.completed < progress.target && !stopping) {
          if (state().status === "finished") { emit({outcome: "finished"}); break; }
          emit({stage: "input"});
          await wait();
          if (stopping) break;
          if (state().status === "ready") {
            emit({stage: "decision"});
            checkResult(await execute("decide", state()), ["pending"]);
            await wait();
          }
          if (stopping) break;
          if (state().status !== "pending") throw new Error("決策狀態已變更，自動執行已停止。");
          emit({stage: "execution"});
          checkResult(await execute("advance", state()), ["ready", "finished"]);
          emit({completed: progress.completed + 1, stage: "settled"});
          if (state().status === "finished") { emit({outcome: "finished"}); break; }
          if (progress.completed < progress.target && !stopping) await wait();
        }
        if (!progress.outcome) emit({outcome: stopping ? "stopped" : "complete"});
      } catch (error) {
        emit({outcome: "error", error: error.message});
      } finally {
        running = false;
        stopping = false;
        emit({stage: null});
      }
    }
  };
}

if (typeof module !== "undefined" && module.exports) module.exports = {createReplay};
else globalThis.PaperReplay = {createReplay};
