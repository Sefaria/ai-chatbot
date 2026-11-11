<script>
  // Lab-only "Profile" tab of the settings panel. LCChatbot renders it only when the
  // server says overrideAllowedForYou. Copy is English only: it is a lab tool, not product UI.
  import { personalizationProfileAction } from '../lib/api.js';

  let { apiBaseUrl, userId } = $props();

  let profile = $state(null);
  let loading = $state(false);
  let error = $state('');

  // Slot editing
  let editingSlot = $state('');
  let editValue = $state('');
  let editIsList = $state(false);

  // The job
  let jobRunning = $state(false);
  let candidate = $state(null);
  let jobMessage = $state('');
  let applying = $state(false);

  // Load learner
  let seedMessage = $state('');
  let seedError = $state('');
  let seeding = $state(false);
  const MAX_SEED_TURNS = 60;

  function describe(e) {
    return e?.message || 'Request failed.';
  }

  function slotValueText(value) {
    if (Array.isArray(value)) return value.join(', ');
    return typeof value === 'string' ? value : JSON.stringify(value);
  }

  async function refresh() {
    loading = true;
    error = '';
    try {
      profile = await personalizationProfileAction(apiBaseUrl, userId, 'get');
    } catch (e) {
      error = describe(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (apiBaseUrl) refresh();
  });

  function startEdit(slot) {
    editingSlot = slot.slot;
    editIsList = Array.isArray(slot.value);
    editValue = editIsList ? slot.value.join(', ') : slotValueText(slot.value);
  }

  async function saveEdit() {
    error = '';
    try {
      await personalizationProfileAction(apiBaseUrl, userId, 'set', {
        slot: editingSlot,
        value: editIsList ? editValue.split(',').map((v) => v.trim()).filter(Boolean) : editValue
      });
      editingSlot = '';
      await refresh();
    } catch (e) {
      error = describe(e);
    }
  }

  async function releaseSlot(slot) {
    error = '';
    try {
      await personalizationProfileAction(apiBaseUrl, userId, 'release', { slot: slot.slot });
      await refresh();
    } catch (e) {
      error = describe(e);
    }
  }

  async function runJob() {
    jobRunning = true;
    jobMessage = '';
    error = '';
    candidate = null;
    try {
      const result = await personalizationProfileAction(apiBaseUrl, userId, 'run_job');
      if (result.status === 'empty') {
        jobMessage = 'Nothing to change.';
        candidate = { ...result, empty: true };
      } else {
        candidate = result;
      }
    } catch (e) {
      error = describe(e);
    } finally {
      jobRunning = false;
    }
  }

  async function applyCandidate() {
    applying = true;
    error = '';
    try {
      const result = await personalizationProfileAction(apiBaseUrl, userId, 'apply_candidate');
      candidate = null;
      jobMessage = 'Applied.';
      if (result?.profile) profile = result.profile;
      else await refresh();
    } catch (e) {
      if (e?.status === 409) {
        candidate = null;
        jobMessage = 'The profile changed since the job ran. Run the job again.';
      } else {
        error = describe(e);
      }
    } finally {
      applying = false;
    }
  }

  async function discardCandidate() {
    error = '';
    try {
      await personalizationProfileAction(apiBaseUrl, userId, 'discard_candidate');
      candidate = null;
      jobMessage = 'Discarded.';
    } catch (e) {
      error = describe(e);
    }
  }

  // The file is read in the browser and its turns go to the server. Nothing from it is
  // logged or stored locally; failures say only what kind of problem it was.
  async function loadLearner(event) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    seedMessage = '';
    seedError = '';
    seeding = true;
    try {
      let parsed;
      try {
        parsed = JSON.parse(await file.text());
      } catch {
        seedError = 'That file is not valid JSON.';
        return;
      }
      const turns = Array.isArray(parsed?.turns) ? parsed.turns : null;
      if (!turns || turns.length === 0) {
        seedError = 'The file needs a non-empty "turns" list.';
        return;
      }
      if (turns.length > MAX_SEED_TURNS) {
        seedError = `At most ${MAX_SEED_TURNS} turns can be loaded; this file has ${turns.length}.`;
        return;
      }
      const result = await personalizationProfileAction(apiBaseUrl, userId, 'seed_persona', { turns });
      seedMessage = `seeded ${result?.seeded ?? turns.length} turns`;
      await refresh();
    } catch (e) {
      seedError = describe(e);
    } finally {
      seeding = false;
    }
  }
</script>

<div class="profile-tab" data-testid="lab-profile">
  <div class="profile-toolbar">
    <button class="profile-btn" onclick={refresh} disabled={loading}>Refresh</button>
    {#if profile}<span class="profile-meta">revision {profile.revision}</span>{/if}
  </div>

  {#if error}<div class="profile-error" role="alert">{error}</div>{/if}
  {#if loading && !profile}<div class="profile-muted">Loading profile…</div>{/if}

  {#if profile}
    {#if profile.slots?.length}
      <ul class="slot-list" aria-label="Profile slots">
        {#each profile.slots as slot (slot.slot)}
          <li class="slot-row" data-slot={slot.slot}>
            <div class="slot-head">
              <span class="slot-name">{slot.slot}</span>
              <span class="slot-status">{slot.status}</span>
            </div>
            {#if editingSlot === slot.slot}
              <input class="slot-input" type="text" bind:value={editValue} aria-label={`Value for ${slot.slot}`} />
              <div class="profile-row-actions">
                <button class="profile-btn primary" onclick={saveEdit}>Save</button>
                <button class="profile-btn" onclick={() => (editingSlot = '')}>Cancel</button>
              </div>
            {:else}
              <div class="slot-value">{slotValueText(slot.value)}</div>
              <div class="slot-meta">
                {slot.evidence?.length ?? 0} evidence · updated {slot.updatedAt || 'never'}
              </div>
              <div class="profile-row-actions">
                <button class="profile-btn" onclick={() => startEdit(slot)}>Edit</button>
                {#if slot.status === 'stated'}
                  <button class="profile-btn" onclick={() => releaseSlot(slot)}>Release</button>
                {/if}
              </div>
            {/if}
          </li>
        {/each}
      </ul>
    {:else}
      <div class="profile-muted">No slots yet.</div>
    {/if}
  {/if}

  <section class="profile-section">
    <div class="profile-section-title">Consolidation job</div>
    <div class="profile-row-actions">
      <button class="profile-btn primary" onclick={runJob} disabled={jobRunning || applying}>
        {jobRunning ? 'Running…' : 'Run the job'}
      </button>
    </div>
    {#if jobMessage}<div class="profile-muted" role="status">{jobMessage}</div>{/if}

    {#if candidate}
      <div class="job-result" data-testid="lab-job-result">
        {#if !candidate.empty}
          <ul class="change-list" aria-label="Proposed changes">
            {#each candidate.changes ?? [] as change}
              <li class="change-row">
                <div><strong>{change.slot}</strong>: {slotValueText(change.old)} → {slotValueText(change.new)}</div>
                <div class="slot-meta">{change.reason}</div>
              </li>
            {/each}
          </ul>
          {#if candidate.conflicts?.length}
            <div class="slot-meta">Conflicts: {candidate.conflicts.map(slotValueText).join('; ')}</div>
          {/if}
        {/if}
        <div class="slot-meta">
          {candidate.observationsRead ?? 0} observations read · {candidate.tokens ?? 0} tokens · ${(candidate.costUsd ?? 0).toFixed(4)}
        </div>
        {#if !candidate.empty}
          <div class="profile-row-actions">
            <button class="profile-btn primary" onclick={applyCandidate} disabled={applying}>Apply</button>
            <button class="profile-btn" onclick={discardCandidate} disabled={applying}>Discard</button>
          </div>
        {/if}
      </div>
    {/if}
  </section>

  <section class="profile-section">
    <div class="profile-section-title">Load learner</div>
    <label class="profile-btn file-btn" class:disabled={seeding}>
      Load learner
      <input type="file" accept="application/json,.json" onchange={loadLearner} disabled={seeding} aria-label="Load learner file" />
    </label>
    <div class="slot-meta">A local JSON file: label and turns. It is read in this browser only.</div>
    {#if seedMessage}<div class="profile-muted" role="status">{seedMessage}</div>{/if}
    {#if seedError}<div class="profile-error" role="alert">{seedError}</div>{/if}
  </section>
</div>

<style>
  .profile-tab { display: flex; flex-direction: column; gap: 12px; font-family: var(--lc-font); }
  .profile-toolbar { display: flex; align-items: center; gap: 10px; }
  .profile-meta, .profile-muted, .slot-meta { font-size: var(--lc-font-size-sm); color: var(--lc-text-secondary); }
  .profile-error { font-size: var(--lc-font-size-sm); color: var(--lc-error); }
  .slot-list, .change-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
  .slot-row, .job-result {
    display: grid; gap: 4px; padding: 10px 12px;
    border: 1px solid var(--lc-border); border-radius: var(--lc-radius-sm);
    background: var(--lc-bg-secondary);
  }
  .slot-head { display: flex; justify-content: space-between; gap: 8px; }
  .slot-name { font-weight: 600; color: var(--lc-text); font-size: var(--lc-font-size-sm); }
  .slot-status { font-size: var(--lc-font-size-sm); color: var(--lc-text-muted); }
  .slot-value { color: var(--lc-text); font-size: 13px; overflow-wrap: anywhere; }
  .slot-input {
    border: 1px solid var(--lc-border); border-radius: var(--lc-radius-sm);
    padding: 8px 10px; font-size: 13px; font-family: var(--lc-font);
    color: var(--lc-text); background: var(--lc-bg-secondary);
  }
  .profile-section { display: grid; gap: 8px; }
  .profile-section-title { font-size: var(--lc-font-size); font-weight: 600; color: var(--lc-text); }
  .profile-row-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .change-row { font-size: 13px; color: var(--lc-text); }
  .profile-btn {
    border: 1px solid var(--lc-border); border-radius: var(--lc-radius-sm);
    padding: 6px 12px; font-size: var(--lc-font-size-sm); font-weight: 600;
    font-family: var(--lc-font); cursor: pointer; transition: all 0.15s ease;
    background: var(--lc-bg-tertiary); color: var(--lc-text-secondary);
  }
  .profile-btn:hover:not(:disabled) { background: var(--lc-bg-secondary); color: var(--lc-text); }
  .profile-btn.primary { background: var(--brand-sefaria-blue); color: white; border-color: transparent; }
  .profile-btn.primary:hover:not(:disabled) { background: var(--lc-primary-hover); color: white; }
  .profile-btn:disabled, .file-btn.disabled { opacity: 0.6; cursor: not-allowed; }
  .file-btn { position: relative; display: inline-block; width: fit-content; }
  .file-btn input { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; }
</style>
