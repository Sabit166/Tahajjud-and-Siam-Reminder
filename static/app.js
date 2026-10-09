/**
 * Dhikr & Tahajjud Bot — Mobile-First Poll & Schedule Manager
 * Client Application Logic
 */

(function () {
  'use strict';

  // --- API & Auth State ---
  const API_BASE = '';
  let authToken = localStorage.getItem('admin_token') || '';
  let allPolls = [];
  let prayerTimings = {};
  let currentFilter = 'all';
  let searchQuery = '';
  let pendingDeleteId = null;

  // Weekday names mapping
  const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const PRAYER_ORDER = ['fajr', 'sunrise', 'dhuhr', 'asr', 'maghrib', 'isha'];

  // --- DOM Elements ---
  const authOverlay = document.getElementById('auth-overlay');
  const pinInput = document.getElementById('pin-input');
  const pinSubmitBtn = document.getElementById('pin-submit-btn');
  const authError = document.getElementById('auth-error');

  const liveTimeEl = document.getElementById('live-time');
  const currentDateEl = document.getElementById('current-date-label');
  const currentDayEl = document.getElementById('current-day-label');
  const nextPrayerBadge = document.getElementById('next-prayer-badge');

  const searchInput = document.getElementById('search-input');
  const clearSearchBtn = document.getElementById('clear-search-btn');
  const filterTabsContainer = document.getElementById('filter-tabs');

  const pollsListEl = document.getElementById('polls-list');
  const pollsLoadingEl = document.getElementById('polls-loading');
  const pollsEmptyEl = document.getElementById('polls-empty');
  const filteredCountText = document.getElementById('filtered-count-text');

  const toastContainer = document.getElementById('toast-container');
  const refreshBtn = document.getElementById('refresh-btn');
  const addPollHeaderBtn = document.getElementById('add-poll-header-btn');
  const fabAddBtn = document.getElementById('fab-add-btn');
  const emptyAddBtn = document.getElementById('empty-add-btn');

  // Modal Elements
  const pollModal = document.getElementById('poll-modal');
  const modalSheet = document.getElementById('modal-sheet');
  const modalTitle = document.getElementById('modal-title');
  const modalCloseBtn = document.getElementById('modal-close-btn');
  const modalCancelBtn = document.getElementById('modal-cancel-btn');
  const pollForm = document.getElementById('poll-form');

  const formPollId = document.getElementById('form-poll-id');
  const formTitle = document.getElementById('form-title');
  const formType = document.getElementById('form-type');
  const formTypeSegments = document.getElementById('form-type-segments');

  const formTimeMode = document.getElementById('form-time-mode');
  const formTimeModeSegments = document.getElementById('form-time-mode-segments');
  const panelPrayerRelative = document.getElementById('panel-prayer-relative');
  const panelFixedTime = document.getElementById('panel-fixed-time');

  const formPrayerName = document.getElementById('form-prayer-name');
  const formOffsetDir = document.getElementById('form-offset-dir');
  const formOffsetMinutes = document.getElementById('form-offset-minutes');
  const offsetMinutesGroup = document.getElementById('offset-minutes-group');
  const previewCalculatedClock = document.getElementById('preview-calculated-clock');
  const previewCalculatedDesc = document.getElementById('preview-calculated-desc');
  const formFixedTime = document.getElementById('form-fixed-time');

  const weekdaySelector = document.getElementById('weekday-selector');
  const weightGroup = document.getElementById('weight-group');
  const formWeight = document.getElementById('form-weight');
  const weightDisplay = document.getElementById('weight-display');

  const optionsGroup = document.getElementById('options-group');
  const optionsListContainer = document.getElementById('options-list-container');
  const addOptionBtn = document.getElementById('add-option-btn');
  const formIsActive = document.getElementById('form-is-active');

  // Confirm Dialog
  const confirmModal = document.getElementById('confirm-modal');
  const confirmCancelBtn = document.getElementById('confirm-cancel-btn');
  const confirmDeleteBtn = document.getElementById('confirm-delete-btn');
  const confirmText = document.getElementById('confirm-text');

  // --- Telegram Web App Support ---
  const isTelegramWebApp = Boolean(window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData);
  if (isTelegramWebApp) {
    try {
      window.Telegram.WebApp.ready();
      window.Telegram.WebApp.expand();
      // Auto-set Telegram theme hints if available
      if (window.Telegram.WebApp.headerColor) {
        window.Telegram.WebApp.setHeaderColor('#090d16');
      }
    } catch (e) {
      console.warn('Telegram WebApp init:', e);
    }
  }

  function triggerHaptic(type = 'light') {
    if (isTelegramWebApp && window.Telegram.WebApp.HapticFeedback) {
      if (type === 'success' || type === 'error' || type === 'warning') {
        window.Telegram.WebApp.HapticFeedback.notificationOccurred(type);
      } else {
        window.Telegram.WebApp.HapticFeedback.impactOccurred(type);
      }
    }
  }

  // --- Toast System ---
  function showToast(message, type = 'info', duration = 3500) {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    const icon = type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️';
    toast.innerHTML = `<span>${icon}</span><span>${message}</span>`;
    toastContainer.appendChild(toast);

    triggerHaptic(type === 'success' ? 'success' : type === 'error' ? 'error' : 'light');

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(-10px)';
      toast.style.transition = 'all 0.25s ease-out';
      setTimeout(() => toast.remove(), 260);
    }, duration);
  }

  // --- API Request Wrapper ---
  async function apiRequest(endpoint, options = {}) {
    const headers = options.headers || {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    headers['Content-Type'] = 'application/json';

    const resp = await fetch(`${API_BASE}${endpoint}`, {
      ...options,
      headers,
    });

    if (resp.status === 401) {
      authToken = '';
      localStorage.removeItem('admin_token');
      showAuthScreen();
      throw new Error('Authentication required');
    }

    if (!resp.ok) {
      const errData = await resp.json().catch(() => ({}));
      throw new Error(errData.detail || `Request failed with status ${resp.status}`);
    }

    return resp.json();
  }

  // --- Authentication Flow ---
  function showAuthScreen() {
    authOverlay.style.display = 'flex';
    pinInput.value = '';
    authError.style.display = 'none';
    setTimeout(() => pinInput.focus(), 200);
  }

  function hideAuthScreen() {
    authOverlay.style.display = 'none';
  }

  async function handlePinSubmit() {
    const pin = pinInput.value.trim();
    if (!pin) return;

    try {
      const res = await fetch(`${API_BASE}/api/auth/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin }),
      });

      if (res.ok) {
        const data = await res.json();
        authToken = data.token;
        localStorage.setItem('admin_token', authToken);
        hideAuthScreen();
        showToast('Admin access granted!', 'success');
        initDashboard();
      } else {
        authError.style.display = 'block';
        triggerHaptic('error');
      }
    } catch (err) {
      authError.textContent = 'Server connection error';
      authError.style.display = 'block';
    }
  }

  pinSubmitBtn.addEventListener('click', handlePinSubmit);
  pinInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handlePinSubmit();
  });

  // --- Live Clock & Prayer Timings ---
  function updateLiveClock() {
    const now = new Date();
    // Dhaka is UTC+6
    const dhakaOffset = 6 * 60;
    const localOffset = now.getTimezoneOffset();
    const dhakaTime = new Date(now.getTime() + (dhakaOffset + localOffset) * 60 * 1000);

    const hours = String(dhakaTime.getHours()).padStart(2, '0');
    const minutes = String(dhakaTime.getMinutes()).padStart(2, '0');
    const seconds = String(dhakaTime.getSeconds()).padStart(2, '0');
    liveTimeEl.textContent = `${hours}:${minutes}:${seconds}`;

    const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    currentDayEl.textContent = days[dhakaTime.getDay()];
    currentDateEl.textContent = `${dhakaTime.getDate()} ${months[dhakaTime.getMonth()]} ${dhakaTime.getFullYear()}`;
  }
  setInterval(updateLiveClock, 1000);
  updateLiveClock();

  async function fetchPrayerTimes() {
    try {
      const data = await apiRequest('/api/prayer-times');
      prayerTimings = data.timings || {};

      PRAYER_ORDER.forEach((p) => {
        const el = document.getElementById(`time-${p}`);
        if (el && prayerTimings[p]) {
          el.textContent = prayerTimings[p];
        }
      });

      highlightUpcomingPrayer();
    } catch (err) {
      console.warn('Failed to load prayer times:', err);
    }
  }

  function highlightUpcomingPrayer() {
    if (!prayerTimings || Object.keys(prayerTimings).length === 0) return;

    const now = new Date();
    const dhakaOffset = 6 * 60;
    const localOffset = now.getTimezoneOffset();
    const dhakaTime = new Date(now.getTime() + (dhakaOffset + localOffset) * 60 * 1000);
    const currentMins = dhakaTime.getHours() * 60 + dhakaTime.getMinutes();

    let nextPrayer = null;
    let minDiff = Infinity;

    PRAYER_ORDER.forEach((p) => {
      const card = document.querySelector(`.prayer-card[data-prayer="${p}"]`);
      if (card) card.classList.remove('upcoming');

      const raw = prayerTimings[p];
      if (raw) {
        const [h, m] = raw.split(':').map(Number);
        const pMins = h * 60 + m;
        const diff = pMins - currentMins;

        if (diff > 0 && diff < minDiff) {
          minDiff = diff;
          nextPrayer = p;
        }
      }
    });

    if (!nextPrayer) {
      // Past Isha -> Next is Fajr
      nextPrayer = 'fajr';
    }

    const nextCard = document.querySelector(`.prayer-card[data-prayer="${nextPrayer}"]`);
    if (nextCard) {
      nextCard.classList.add('upcoming');
    }

    const cap = nextPrayer.charAt(0).toUpperCase() + nextPrayer.slice(1);
    nextPrayerBadge.textContent = `Next: ${cap} (${prayerTimings[nextPrayer] || '--:--'})`;
  }

  // --- Polls Fetch & Render ---
  async function fetchPolls() {
    pollsLoadingEl.style.display = 'block';
    pollsEmptyEl.style.display = 'none';
    pollsListEl.innerHTML = '';

    try {
      const data = await apiRequest('/api/polls');
      allPolls = data.polls || [];
      updateFilterCounts();
      renderPollsList();
    } catch (err) {
      console.error('Fetch polls error:', err);
      showToast(err.message, 'error');
    } finally {
      pollsLoadingEl.style.display = 'none';
    }
  }

  function updateFilterCounts() {
    const total = allPolls.length;
    const amals = allPolls.filter((p) => p.poll_type === 'amal_poll').length;
    const reports = allPolls.filter((p) => p.poll_type !== 'amal_poll').length;
    const active = allPolls.filter((p) => p.is_active).length;
    const paused = total - active;

    document.getElementById('count-all').textContent = total;
    document.getElementById('count-amal').textContent = amals;
    document.getElementById('count-reports').textContent = reports;
    document.getElementById('count-active').textContent = active;
    document.getElementById('count-paused').textContent = paused;
  }

  function renderPollsList() {
    pollsListEl.innerHTML = '';

    let filtered = allPolls.slice();

    // Filter chip
    if (currentFilter === 'amal_poll') {
      filtered = filtered.filter((p) => p.poll_type === 'amal_poll');
    } else if (currentFilter === 'reports') {
      filtered = filtered.filter((p) => p.poll_type !== 'amal_poll');
    } else if (currentFilter === 'active') {
      filtered = filtered.filter((p) => p.is_active);
    } else if (currentFilter === 'paused') {
      filtered = filtered.filter((p) => !p.is_active);
    }

    // Search query
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      filtered = filtered.filter(
        (p) =>
          p.title.toLowerCase().includes(q) ||
          p.id.toLowerCase().includes(q) ||
          (p.readable_schedule && p.readable_schedule.toLowerCase().includes(q))
      );
    }

    filteredCountText.textContent = `Showing ${filtered.length} of ${allPolls.length} items`;

    if (filtered.length === 0) {
      pollsEmptyEl.style.display = 'block';
      return;
    }
    pollsEmptyEl.style.display = 'none';

    filtered.forEach((poll) => {
      const card = createPollCard(poll);
      pollsListEl.appendChild(card);
    });
  }

  function createPollCard(poll) {
    const card = document.createElement('article');
    card.className = `poll-card ${poll.is_active ? 'active' : 'inactive'}`;
    card.dataset.id = poll.id;

    // Type label & badge color
    let typeBadgeClass = 'badge-emerald';
    let typeLabel = 'Amal Poll';
    if (poll.poll_type === 'report') {
      typeBadgeClass = 'badge-amber';
      typeLabel = 'Report';
    } else if (poll.poll_type === 'reminder') {
      typeBadgeClass = 'badge-purple';
      typeLabel = 'Sunnah Reminder';
    }

    // Schedule description & clock badge
    const isFixed = poll.time_type === 'fixed';
    const scheduleDesc = poll.readable_schedule || (isFixed ? `Fixed ${poll.fixed_time}` : 'Prayer relative');
    const clockTime = poll.calculated_time_today || (isFixed ? poll.fixed_time : '--:--');

    // Weekday mini pills
    const activeDays = poll.days_of_week || [0, 1, 2, 3, 4, 5, 6];
    const dayChars = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
    const weekdaysHtml = dayChars
      .map((ch, idx) => `<span class="day-pill-mini ${activeDays.includes(idx) ? 'active' : ''}">${ch}</span>`)
      .join('');

    // Poll options chips
    const options = poll.poll_options || [];
    const optionsHtml = options
      .slice(0, 3)
      .map((opt) => `<span class="option-chip-mini">✓ ${escapeHtml(opt)}</span>`)
      .join('');

    card.innerHTML = `
      <div class="card-header-row">
        <div class="card-title-group">
          <div class="card-type-row">
            <span class="badge ${typeBadgeClass}">${typeLabel}</span>
            ${poll.is_scheduled_today ? '<span class="badge badge-emerald">Today</span>' : ''}
          </div>
          <h3 class="poll-title">${escapeHtml(poll.title)}</h3>
        </div>
        <label class="switch" title="Toggle active status">
          <input type="checkbox" class="card-active-toggle" ${poll.is_active ? 'checked' : ''}>
          <span class="slider round"></span>
        </label>
      </div>

      <div class="card-timing-box">
        <div class="timing-desc-wrap">
          <span class="timing-icon">${isFixed ? '⏰' : '🕌'}</span>
          <span>${escapeHtml(scheduleDesc)}</span>
        </div>
        <div class="clock-preview-badge ${isFixed ? 'fixed' : ''}">
          <span>⏰</span>
          <span>${clockTime}</span>
        </div>
      </div>

      <div class="card-meta-row">
        <div class="weekdays-mini-strip" title="Active on: ${activeDays.map((d) => WEEKDAYS[d]).join(', ')}">
          ${weekdaysHtml}
        </div>
        ${
          poll.poll_type === 'amal_poll'
            ? `<div class="marks-badge">⭐ ${poll.weight || 1} Marks</div>`
            : ''
        }
      </div>

      ${optionsHtml ? `<div class="card-options-strip">${optionsHtml}</div>` : ''}

      <div class="card-actions-row">
        <button class="btn-send-now" title="Dispatch poll to Telegram right now">
          <span>⚡</span>
          <span>Send Now</span>
        </button>
        <button class="btn-edit-card" title="Edit schedule and parameters">
          <span>✏️ Edit</span>
        </button>
        <button class="btn-delete-card" title="Delete this poll">
          <span>🗑️</span>
        </button>
      </div>
    `;

    // Bind card events
    const toggleInput = card.querySelector('.card-active-toggle');
    toggleInput.addEventListener('change', async () => {
      triggerHaptic('light');
      try {
        await apiRequest(`/api/polls/${poll.id}/toggle`, { method: 'POST' });
        poll.is_active = toggleInput.checked;
        card.classList.toggle('inactive', !poll.is_active);
        updateFilterCounts();
        showToast(`Poll ${poll.is_active ? 'activated' : 'paused'}`, 'info');
      } catch (err) {
        toggleInput.checked = !toggleInput.checked;
        showToast(err.message, 'error');
      }
    });

    const sendNowBtn = card.querySelector('.btn-send-now');
    sendNowBtn.addEventListener('click', async () => {
      triggerHaptic('medium');
      sendNowBtn.disabled = true;
      sendNowBtn.innerHTML = '<span>⏳ Sending...</span>';
      try {
        const res = await apiRequest(`/api/polls/${poll.id}/trigger`, { method: 'POST' });
        showToast(res.message || `Dispatched '${poll.title}' to Telegram!`, 'success');
        triggerHaptic('success');
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        sendNowBtn.disabled = false;
        sendNowBtn.innerHTML = '<span>⚡</span><span>Send Now</span>';
      }
    });

    const editBtn = card.querySelector('.btn-edit-card');
    editBtn.addEventListener('click', () => {
      triggerHaptic('light');
      openEditModal(poll);
    });

    const deleteBtn = card.querySelector('.btn-delete-card');
    deleteBtn.addEventListener('click', () => {
      triggerHaptic('warning');
      openDeleteConfirm(poll);
    });

    return card;
  }

  // --- Modal Form Handlers (Add / Edit) ---
  function openAddModal() {
    triggerHaptic('light');
    formPollId.value = '';
    modalTitle.textContent = 'Add New Poll';
    formTitle.value = '';

    // Default: Amal Poll
    setSegmentValue(formTypeSegments, 'amal_poll');
    formType.value = 'amal_poll';

    // Default: Prayer relative
    setSegmentValue(formTimeModeSegments, 'prayer_relative');
    formTimeMode.value = 'prayer_relative';
    panelPrayerRelative.style.display = 'block';
    panelFixedTime.style.display = 'none';

    formPrayerName.value = 'maghrib';
    formOffsetDir.value = 'after';
    formOffsetMinutes.value = '30';
    offsetMinutesGroup.style.display = 'block';

    // Fixed time default
    formFixedTime.value = '10:00';

    // Weekdays default: All
    setWeekdayPills([0, 1, 2, 3, 4, 5, 6]);

    // Weight default
    formWeight.value = '7';
    weightDisplay.textContent = '⭐ 7 marks';
    weightGroup.style.display = 'block';

    // Options default
    renderOptionsInputs(['Alhamdulillah, done', 'Incomplete/Missed']);
    optionsGroup.style.display = 'block';

    formIsActive.checked = true;

    updatePrayerCalculationPreview();
    pollModal.style.display = 'flex';
  }

  function openEditModal(poll) {
    triggerHaptic('light');
    formPollId.value = poll.id;
    modalTitle.textContent = 'Edit Poll Schedule';
    formTitle.value = poll.title;

    // Type
    setSegmentValue(formTypeSegments, poll.poll_type || 'amal_poll');
    formType.value = poll.poll_type || 'amal_poll';
    toggleTypeDependentSections(formType.value);

    // Timing Mode
    const timeMode = poll.time_type || 'prayer_relative';
    setSegmentValue(formTimeModeSegments, timeMode);
    formTimeMode.value = timeMode;

    if (timeMode === 'fixed') {
      panelPrayerRelative.style.display = 'none';
      panelFixedTime.style.display = 'block';
      formFixedTime.value = poll.fixed_time || '10:00';
    } else {
      panelPrayerRelative.style.display = 'block';
      panelFixedTime.style.display = 'none';

      formPrayerName.value = (poll.prayer_name || 'maghrib').toLowerCase();
      const offset = Number(poll.prayer_offset_minutes || 0);
      if (offset === 0) {
        formOffsetDir.value = 'at';
        offsetMinutesGroup.style.display = 'none';
      } else if (offset < 0) {
        formOffsetDir.value = 'before';
        formOffsetMinutes.value = Math.abs(offset);
        offsetMinutesGroup.style.display = 'block';
      } else {
        formOffsetDir.value = 'after';
        formOffsetMinutes.value = offset;
        offsetMinutesGroup.style.display = 'block';
      }
    }

    // Weekdays
    setWeekdayPills(poll.days_of_week || [0, 1, 2, 3, 4, 5, 6]);

    // Weight
    formWeight.value = poll.weight !== undefined ? poll.weight : 7;
    weightDisplay.textContent = `⭐ ${formWeight.value} marks`;

    // Options
    renderOptionsInputs(poll.poll_options || ['Alhamdulillah, done', 'Incomplete/Missed']);

    formIsActive.checked = poll.is_active !== false;

    updatePrayerCalculationPreview();
    pollModal.style.display = 'flex';
  }

  function closeModal() {
    pollModal.style.display = 'none';
  }

  modalCloseBtn.addEventListener('click', closeModal);
  modalCancelBtn.addEventListener('click', closeModal);
  pollModal.addEventListener('click', (e) => {
    if (e.target === pollModal) closeModal();
  });

  // Type Segments Switch
  formTypeSegments.addEventListener('click', (e) => {
    const btn = e.target.closest('.segment-btn');
    if (!btn) return;
    triggerHaptic('light');
    setSegmentValue(formTypeSegments, btn.dataset.type);
    formType.value = btn.dataset.type;
    toggleTypeDependentSections(formType.value);
  });

  function toggleTypeDependentSections(type) {
    if (type === 'amal_poll') {
      weightGroup.style.display = 'block';
      optionsGroup.style.display = 'block';
    } else {
      weightGroup.style.display = 'none';
      optionsGroup.style.display = 'none';
    }
  }

  // Timing Mode Segments Switch
  formTimeModeSegments.addEventListener('click', (e) => {
    const btn = e.target.closest('.segment-btn');
    if (!btn) return;
    triggerHaptic('light');
    setSegmentValue(formTimeModeSegments, btn.dataset.mode);
    formTimeMode.value = btn.dataset.mode;

    if (formTimeMode.value === 'fixed') {
      panelPrayerRelative.style.display = 'none';
      panelFixedTime.style.display = 'block';
    } else {
      panelPrayerRelative.style.display = 'block';
      panelFixedTime.style.display = 'none';
      updatePrayerCalculationPreview();
    }
  });

  function setSegmentValue(container, value) {
    container.querySelectorAll('.segment-btn').forEach((b) => {
      b.classList.toggle('active', b.dataset.type === value || b.dataset.mode === value);
    });
  }

  // Prayer Calculation Live Preview
  function updatePrayerCalculationPreview() {
    const prayer = formPrayerName.value.toLowerCase();
    const dir = formOffsetDir.value;
    let offset = parseInt(formOffsetMinutes.value, 10) || 0;
    if (dir === 'before') offset = -offset;
    if (dir === 'at') offset = 0;

    offsetMinutesGroup.style.display = dir === 'at' ? 'none' : 'block';

    const pTime = prayerTimings[prayer];
    if (!pTime) {
      previewCalculatedClock.textContent = '--:--';
      previewCalculatedDesc.textContent = `Relative to ${prayer}`;
      return;
    }

    const [h, m] = pTime.split(':').map(Number);
    const date = new Date();
    date.setHours(h, m + offset, 0, 0);

    const calcH = String(date.getHours()).padStart(2, '0');
    const calcM = String(date.getMinutes()).padStart(2, '0');
    previewCalculatedClock.textContent = `${calcH}:${calcM}`;

    const prayerCap = prayer.charAt(0).toUpperCase() + prayer.slice(1);
    if (offset === 0) {
      previewCalculatedDesc.textContent = `Exactly at ${prayerCap} (${pTime})`;
    } else if (offset > 0) {
      previewCalculatedDesc.textContent = `${offset} min after ${prayerCap} (${pTime})`;
    } else {
      previewCalculatedDesc.textContent = `${Math.abs(offset)} min before ${prayerCap} (${pTime})`;
    }
  }

  formPrayerName.addEventListener('change', updatePrayerCalculationPreview);
  formOffsetDir.addEventListener('change', updatePrayerCalculationPreview);
  formOffsetMinutes.addEventListener('input', updatePrayerCalculationPreview);

  // Quick Minutes Chips
  document.querySelectorAll('.quick-minutes-chips .chip-btn').forEach((chip) => {
    chip.addEventListener('click', () => {
      triggerHaptic('light');
      formOffsetMinutes.value = chip.dataset.min;
      updatePrayerCalculationPreview();
    });
  });

  // Weekday Selector
  function setWeekdayPills(activeList) {
    weekdaySelector.querySelectorAll('.day-btn').forEach((btn) => {
      const day = parseInt(btn.dataset.day, 10);
      btn.classList.toggle('active', activeList.includes(day));
    });
  }

  weekdaySelector.addEventListener('click', (e) => {
    const btn = e.target.closest('.day-btn');
    if (!btn) return;
    triggerHaptic('light');
    btn.classList.toggle('active');
  });

  // Weekday Presets
  document.querySelectorAll('.quick-days-presets .preset-link').forEach((link) => {
    link.addEventListener('click', () => {
      triggerHaptic('light');
      const preset = link.dataset.days;
      if (preset === 'all') setWeekdayPills([0, 1, 2, 3, 4, 5, 6]);
      if (preset === 'sawm') setWeekdayPills([0, 3]); // Mon & Thu
      if (preset === 'friday') setWeekdayPills([4]); // Friday
    });
  });

  // Marks / Weight Slider
  formWeight.addEventListener('input', () => {
    weightDisplay.textContent = `⭐ ${formWeight.value} marks`;
  });

  // Dynamic Poll Options
  function renderOptionsInputs(options) {
    optionsListContainer.innerHTML = '';
    options.forEach((optText, index) => {
      const row = document.createElement('div');
      row.className = 'option-row';
      row.innerHTML = `
        <input type="text" class="form-control form-option-input" value="${escapeHtml(optText)}" placeholder="Option ${index + 1}" required>
        ${
          options.length > 2
            ? '<button type="button" class="option-remove-btn" title="Remove option">✕</button>'
            : ''
        }
      `;
      if (options.length > 2) {
        row.querySelector('.option-remove-btn').addEventListener('click', () => {
          row.remove();
        });
      }
      optionsListContainer.appendChild(row);
    });
  }

  addOptionBtn.addEventListener('click', () => {
    triggerHaptic('light');
    const row = document.createElement('div');
    row.className = 'option-row';
    const count = optionsListContainer.children.length + 1;
    row.innerHTML = `
      <input type="text" class="form-control form-option-input" placeholder="Option ${count}" required>
      <button type="button" class="option-remove-btn" title="Remove option">✕</button>
    `;
    row.querySelector('.option-remove-btn').addEventListener('click', () => {
      row.remove();
    });
    optionsListContainer.appendChild(row);
    row.querySelector('input').focus();
  });

  // Form Submit (Save / Create)
  pollForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    triggerHaptic('medium');

    const pollId = formPollId.value.trim();
    const title = formTitle.value.trim();
    const pollType = formType.value;
    const timeType = formTimeMode.value;

    if (!title) {
      showToast('Poll title is required', 'error');
      return;
    }

    // Days of week
    const activeDays = [];
    weekdaySelector.querySelectorAll('.day-btn.active').forEach((btn) => {
      activeDays.push(parseInt(btn.dataset.day, 10));
    });
    if (activeDays.length === 0) {
      showToast('Select at least one active day of the week', 'error');
      return;
    }

    // Collect options
    const options = [];
    if (pollType === 'amal_poll') {
      optionsListContainer.querySelectorAll('.form-option-input').forEach((inp) => {
        const val = inp.value.trim();
        if (val) options.push(val);
      });
      if (options.length < 2) {
        showToast('Polls require at least 2 answer options', 'error');
        return;
      }
    }

    // Build payload
    const payload = {
      title,
      poll_type: pollType,
      poll_options: options,
      weight: parseInt(formWeight.value, 10) || 1,
      time_type: timeType,
      days_of_week: activeDays,
      is_active: formIsActive.checked,
    };

    if (timeType === 'fixed') {
      const fixedTime = formFixedTime.value;
      if (!fixedTime) {
        showToast('Specify fixed clock time', 'error');
        return;
      }
      payload.fixed_time = fixedTime;
      payload.prayer_name = null;
      payload.prayer_offset_minutes = 0;
    } else {
      payload.fixed_time = null;
      payload.prayer_name = formPrayerName.value.toLowerCase();
      let offset = parseInt(formOffsetMinutes.value, 10) || 0;
      if (formOffsetDir.value === 'before') offset = -offset;
      if (formOffsetDir.value === 'at') offset = 0;
      payload.prayer_offset_minutes = offset;
    }

    const saveBtn = document.getElementById('modal-save-btn');
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';

    try {
      if (pollId) {
        // Update
        await apiRequest(`/api/polls/${pollId}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        });
        showToast(`Updated '${title}' schedule successfully!`, 'success');
      } else {
        // Create
        await apiRequest('/api/polls', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        showToast(`Created new poll '${title}'!`, 'success');
      }

      closeModal();
      await fetchPolls();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save Poll Schedule';
    }
  });

  // --- Confirm Delete Modal ---
  function openDeleteConfirm(poll) {
    pendingDeleteId = poll.id;
    confirmText.textContent = `Are you sure you want to delete "${poll.title}"? This cannot be undone.`;
    confirmModal.style.display = 'flex';
  }

  function closeDeleteConfirm() {
    confirmModal.style.display = 'none';
    pendingDeleteId = null;
  }

  confirmCancelBtn.addEventListener('click', closeDeleteConfirm);
  confirmModal.addEventListener('click', (e) => {
    if (e.target === confirmModal) closeDeleteConfirm();
  });

  confirmDeleteBtn.addEventListener('click', async () => {
    if (!pendingDeleteId) return;
    triggerHaptic('medium');
    confirmDeleteBtn.disabled = true;
    confirmDeleteBtn.textContent = 'Deleting...';

    try {
      await apiRequest(`/api/polls/${pendingDeleteId}`, { method: 'DELETE' });
      showToast('Poll deleted successfully', 'success');
      closeDeleteConfirm();
      await fetchPolls();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      confirmDeleteBtn.disabled = false;
      confirmDeleteBtn.textContent = 'Yes, Delete';
    }
  });

  // --- Filter Tabs and Search ---
  filterTabsContainer.addEventListener('click', (e) => {
    const tab = e.target.closest('.tab-chip');
    if (!tab) return;
    triggerHaptic('light');
    filterTabsContainer.querySelectorAll('.tab-chip').forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');
    currentFilter = tab.dataset.filter;
    renderPollsList();
  });

  searchInput.addEventListener('input', (e) => {
    searchQuery = e.target.value.trim();
    clearSearchBtn.style.display = searchQuery ? 'block' : 'none';
    renderPollsList();
  });

  clearSearchBtn.addEventListener('click', () => {
    searchInput.value = '';
    searchQuery = '';
    clearSearchBtn.style.display = 'none';
    renderPollsList();
  });

  // --- Top Buttons ---
  addPollHeaderBtn.addEventListener('click', openAddModal);
  fabAddBtn.addEventListener('click', openAddModal);
  emptyAddBtn.addEventListener('click', openAddModal);

  refreshBtn.addEventListener('click', async () => {
    triggerHaptic('light');
    refreshBtn.style.transform = 'rotate(180deg)';
    refreshBtn.style.transition = 'transform 0.4s ease';
    await fetchPrayerTimes();
    await fetchPolls();
    setTimeout(() => {
      refreshBtn.style.transform = 'none';
    }, 400);
    showToast('Refreshed prayer times and poll schedules', 'info');
  });

  // --- HTML escaping helper ---
  function escapeHtml(text) {
    if (!text) return '';
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // --- Init Dashboard ---
  async function initDashboard() {
    await fetchPrayerTimes();
    await fetchPolls();
  }

  // Check initial authentication
  async function checkInitialAuth() {
    try {
      const res = await fetch(`${API_BASE}/api/auth/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pin: authToken,
          telegram_init_data: isTelegramWebApp ? window.Telegram.WebApp.initData : null,
          telegram_user_id: isTelegramWebApp ? window.Telegram.WebApp.initDataUnsafe?.user?.id : null,
        }),
      });

      if (res.ok) {
        const data = await res.json();
        authToken = data.token;
        localStorage.setItem('admin_token', authToken);
        hideAuthScreen();
        initDashboard();
      } else {
        showAuthScreen();
      }
    } catch (err) {
      showAuthScreen();
    }
  }

  checkInitialAuth();
})();
