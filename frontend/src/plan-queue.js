// A cancelled browser fetch does not cancel server computation. Finish the
// active request, retain only the newest pending input, and hide stale results.
export function createLatestPlanQueue({onResult,onError,onBusy}) {
  let active=null,pending=null,generation=0,closed=false;
  async function start(job) {
    active=job;onBusy(true);
    try {
      const result=await job.run();
      if(!closed&&job.generation===generation)onResult(result);
    } catch(error) {
      if(!closed&&job.generation===generation)onError(error);
    } finally {
      active=null;
      const next=pending;pending=null;
      if(!closed&&next&&next.generation===generation)start(next);
      else if(!closed)onBusy(false);
    }
  }
  return {
    submit(key,run) {
      if(closed)return;
      // Returning to the active input also supersedes a different pending one.
      if(active?.key===key&&active.generation===generation){pending=null;return;}
      const job={key,run,generation};
      if(active)pending=job;else start(job);
    },
    invalidate(){generation++;pending=null;},
    clearPending(){pending=null;},
    close(){closed=true;generation++;pending=null;},
  };
}
