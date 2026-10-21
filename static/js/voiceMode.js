const PREF_KEY = 'voice_themes';
const DEFAULT_THEMES = [
  { id: 'signal', name: 'Signal', base: 'signal', color: '#9cdef2', builtin: true },
  { id: 'matrix', name: 'Dot matrix', base: 'matrix', color: '#9cdef2', builtin: true },
];
const MAX_CUSTOM_THEMES = 16;

function safeColor(value) {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value) ? value : '#9cdef2';
}

class VoiceMode {
  constructor() {
    this.active = false;
    this.state = 'idle';
    this.recorder = null;
    this.ui = null;
    this.themes = [...DEFAULT_THEMES];
    this.selectedTheme = 'signal';
    this.customThemes = [];
    this.animation = null;
    this.ttsWasAutoPlay = false;
    this.pendingRearm = null;
  }

  init(recorder, ui, settings) {
    this.recorder = recorder;
    this.ui = ui;
    this.settings = settings;
    this.overlay = document.getElementById('voice-mode-overlay');
    this.canvas = document.getElementById('voice-mode-visual');
    if (!this.overlay || !this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    document.getElementById('voice-mode-open')?.addEventListener('click', () => this.open());
    document.getElementById('voice-mode-close')?.addEventListener('click', () => this.close());
    document.getElementById('voice-mode-mic')?.addEventListener('click', () => this.toggleRecording());
    document.getElementById('voice-mode-stop-audio')?.addEventListener('click', () => window.aiTTSManager?.stop());
    document.getElementById('voice-theme-select')?.addEventListener('change', ev => this.selectTheme(ev.target.value));
    document.getElementById('voice-theme-add')?.addEventListener('click', () => this.showThemeForm(true));
    document.getElementById('voice-theme-delete')?.addEventListener('click', () => this.deleteSelectedTheme());
    document.getElementById('voice-theme-cancel')?.addEventListener('click', () => this.showThemeForm(false));
    document.getElementById('voice-theme-form')?.addEventListener('submit', ev => this.createTheme(ev));
    window.addEventListener('odysseus-tts-state', ev => this.onTTSState(ev.detail?.state));
    window.addEventListener('odysseus-chat-response', ev => this.showCaption(ev.detail?.text, 'Odysseus'));
    window.addEventListener('odysseus-chat-response-done', () => this.onResponseDone());
    window.addEventListener('odysseus-stt-state', ev => this.onSTTState(ev.detail?.state));
    window.addEventListener('keydown', ev => {
      if (ev.key === 'Escape' && this.active) this.close();
    });
    this.loadThemes();
  }

  async loadThemes() {
    try {
      const res = await fetch(`/api/prefs/${PREF_KEY}`, { credentials: 'same-origin' });
      if (res.ok) {
        const payload = await res.json();
        const saved = payload.value && typeof payload.value === 'object' ? payload.value : {};
        this.customThemes = Array.isArray(saved.customThemes)
          ? saved.customThemes.filter(theme => theme && typeof theme === 'object').slice(0, MAX_CUSTOM_THEMES).map((theme, i) => ({
              id: /^custom-[a-z0-9-]{1,48}$/i.test(theme.id) ? theme.id : `custom-${i}`,
              name: String(theme.name || 'My theme').slice(0, 32),
              base: theme.base === 'matrix' ? 'matrix' : 'signal',
              color: safeColor(theme.color),
              builtin: false,
            }))
          : [];
        this.selectedTheme = String(saved.selected || 'signal');
      }
    } catch (error) {
      console.warn('Could not load voice themes:', error);
    }
    this.renderThemeOptions();
  }

  async saveThemes() {
    const customThemes = this.customThemes.map(({ id, name, base, color }) => ({ id, name, base, color }));
    const res = await fetch(`/api/prefs/${PREF_KEY}`, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: { selected: this.selectedTheme, customThemes } }),
    });
    if (!res.ok) throw new Error('Could not save voice themes');
  }

  renderThemeOptions() {
    const select = document.getElementById('voice-theme-select');
    if (!select) return;
    this.themes = [...DEFAULT_THEMES, ...this.customThemes];
    select.replaceChildren();
    for (const theme of this.themes) {
      const option = document.createElement('option');
      option.value = theme.id;
      option.textContent = theme.name;
      select.appendChild(option);
    }
    if (!this.themes.some(t => t.id === this.selectedTheme)) this.selectedTheme = 'signal';
    select.value = this.selectedTheme;
    this.applyTheme();
    const selected = this.themes.find(theme => theme.id === this.selectedTheme);
    const deleteButton = document.getElementById('voice-theme-delete');
    if (deleteButton) deleteButton.hidden = !selected || selected.builtin;
  }

  async selectTheme(id) {
    if (!this.themes.some(theme => theme.id === id)) return;
    this.selectedTheme = id;
    this.applyTheme();
    try { await this.saveThemes(); } catch (error) { console.warn(error); }
  }

  async deleteSelectedTheme() {
    const index = this.customThemes.findIndex(theme => theme.id === this.selectedTheme);
    if (index < 0) return;
    this.customThemes.splice(index, 1);
    this.selectedTheme = 'signal';
    this.renderThemeOptions();
    try { await this.saveThemes(); } catch (error) { console.warn('Could not save voice theme change:', error); }
  }

  applyTheme() {
    const theme = this.themes.find(item => item.id === this.selectedTheme) || DEFAULT_THEMES[0];
    this.overlay.dataset.voiceTheme = theme.base;
    this.overlay.style.setProperty('--voice-accent', safeColor(theme.color));
  }

  showThemeForm(show) {
    const form = document.getElementById('voice-theme-form');
    if (!form) return;
    form.classList.toggle('hidden', !show);
    if (show) document.getElementById('voice-theme-name')?.focus();
  }

  async createTheme(event) {
    event.preventDefault();
    if (this.customThemes.length >= MAX_CUSTOM_THEMES) {
      this.ui?.showToast?.(`You can save up to ${MAX_CUSTOM_THEMES} custom voice themes.`);
      return;
    }
    const nameInput = document.getElementById('voice-theme-name');
    const name = (nameInput?.value || '').trim().slice(0, 32);
    if (!name) return;
    const base = document.getElementById('voice-theme-base')?.value === 'matrix' ? 'matrix' : 'signal';
    const color = safeColor(document.getElementById('voice-theme-color')?.value);
    this.customThemes.push({ id: `custom-${Date.now()}`, name, base, color, builtin: false });
    this.selectedTheme = this.customThemes[this.customThemes.length - 1].id;
    this.renderThemeOptions();
    this.showThemeForm(false);
    if (nameInput) nameInput.value = '';
    try {
      await this.saveThemes();
      this.ui?.showToast?.('Voice theme saved');
    } catch (error) {
      this.customThemes.pop();
      this.selectedTheme = 'signal';
      this.renderThemeOptions();
      this.ui?.showError?.('Could not save voice theme');
    }
  }

  async open() {
    if (this.active) return;
    await this.recorder?.refreshSttProvider?.();
    if (this.recorder?._sttProvider === 'disabled') {
      this.ui?.showToast?.('Speech input is off. Opening AI Defaults…');
      this.settings?.open?.('ai');
      return;
    }
    this.active = true;
    this.overlay.classList.remove('hidden');
    this.overlay.setAttribute('aria-hidden', 'false');
    document.body.classList.add('voice-mode-open');
    this.ttsWasAutoPlay = !!window.aiTTSManager?.autoPlay;
    if (window.aiTTSManager) {
      await window.aiTTSManager.checkAvailability();
      window.aiTTSManager.autoPlay = window.aiTTSManager.available;
    }
    document.getElementById('voice-mode-close')?.focus();
    this.setState('idle', window.aiTTSManager?.available ? 'Ready when you are' : 'Speech output is not configured');
    this.startAnimation();
  }

  close() {
    if (!this.active) return;
    this.active = false;
    clearTimeout(this.pendingRearm);
    if (this.recorder?.getIsRecording()) this.recorder.stopRecording();
    window.aiTTSManager?.stop();
    if (window.aiTTSManager) window.aiTTSManager.autoPlay = this.ttsWasAutoPlay;
    this.overlay.classList.add('hidden');
    this.overlay.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('voice-mode-open');
    cancelAnimationFrame(this.animation);
    document.getElementById('voice-mode-open')?.focus();
  }

  toggleRecording() {
    if (!this.active) return;
    if (this.recorder?.getIsRecording()) {
      this.recorder.stopRecording();
      return;
    }
    if (window.aiTTSManager?.isPlaying || window.aiTTSManager?._processing) window.aiTTSManager.stop();
    this.setState('listening', 'Listening — tap again when you are done');
    this.recorder.startRecording(
      () => {
        this.setState('idle', 'I couldn’t transcribe that. Check speech input settings.');
      },
      message => this.ui?.showToast?.(message),
      message => {
        this.setState('idle', message);
        this.ui?.showError?.(message);
      },
      transcript => {
        if (!this.active) return;
        this.showCaption(transcript, 'You');
        this.setState('thinking', 'Sending your words to Odysseus…');
        setTimeout(() => {
          if (!this.active) return;
          document.getElementById('chat-form')?.requestSubmit();
        }, 80);
      },
    );
  }

  onSTTState(state) {
    if (!this.active) return;
    if (state === 'processing') this.setState('thinking', 'Transcribing…');
    if (state === 'error') this.setState('error', 'Microphone unavailable');
    if (state === 'ready' && this.state === 'listening') {
      if (document.getElementById('voice-handsfree-toggle')?.checked) {
        this.scheduleRearm();
      } else {
        this.setState('idle', 'Ready for your next turn');
      }
    }
  }

  onTTSState(state) {
    if (!this.active) return;
    if (state === 'speaking') this.setState('speaking', 'Odysseus is speaking');
    if (state === 'idle') {
      document.getElementById('voice-mode-stop-audio').disabled = true;
      if (document.getElementById('voice-handsfree-toggle')?.checked) {
        this.scheduleRearm();
      } else {
        this.setState('idle', 'Ready for your next turn');
      }
    }
    if (state === 'stopped') {
      document.getElementById('voice-mode-stop-audio').disabled = true;
      this.setState('idle', 'Ready for your next turn');
    }
  }

  onResponseDone() {
    if (!this.active) return;
    if (window.aiTTSManager?.isPlaying || window.aiTTSManager?._processing) {
      document.getElementById('voice-mode-stop-audio').disabled = false;
      return;
    }
    document.getElementById('voice-mode-stop-audio').disabled = true;
    this.setState('idle', window.aiTTSManager?.available ? 'Ready for your next turn' : 'Reply complete · speech output is not configured');
    if (document.getElementById('voice-handsfree-toggle')?.checked) {
      this.scheduleRearm();
    }
  }

  scheduleRearm() {
    clearTimeout(this.pendingRearm);
    this.pendingRearm = setTimeout(() => {
      this.pendingRearm = null;
      if (this.active && document.getElementById('voice-handsfree-toggle')?.checked && !this.recorder?.getIsRecording()) {
        this.toggleRecording();
      }
    }, 700);
  }

  showCaption(text, speaker) {
    if (!this.active || typeof text !== 'string' || !text.trim()) return;
    const captionText = text.replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, '').trim();
    if (!captionText) return;
    const caption = document.getElementById('voice-mode-caption');
    if (caption) {
      caption.replaceChildren();
      const label = document.createElement('strong');
      label.textContent = `${speaker}: `;
      caption.append(label, document.createTextNode(captionText.slice(0, 900)));
    }
  }

  setState(state, status) {
    this.state = state;
    this.overlay.dataset.voiceState = state;
    const statusEl = document.getElementById('voice-mode-status');
    if (statusEl && status) statusEl.textContent = status;
    const mic = document.getElementById('voice-mode-mic');
    const recording = state === 'listening';
    mic?.classList.toggle('is-recording', recording);
    mic?.setAttribute('aria-label', recording ? 'Stop listening' : 'Start listening');
    mic?.setAttribute('aria-pressed', String(recording));
    if (state === 'speaking') document.getElementById('voice-mode-stop-audio').disabled = false;
  }

  startAnimation() {
    if (!this.active || !this.ctx) return;
    const canvas = this.canvas;
    const ctx = this.ctx;
    const frame = time => {
      if (!this.active) return;
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.max(1, Math.floor(rect.width * dpr));
      const height = Math.max(1, Math.floor(rect.height * dpr));
      if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, rect.width, rect.height);
      const x = rect.width / 2, y = rect.height / 2, radius = Math.min(rect.width, rect.height) * .27;
      const state = this.state;
      const energy = state === 'speaking' ? 1 : state === 'listening' ? .76 : state === 'thinking' ? .48 : .25;
      const theme = this.themes.find(item => item.id === this.selectedTheme) || DEFAULT_THEMES[0];
      const color = theme.color;
      ctx.save();
      ctx.shadowColor = color;
      ctx.shadowBlur = 28 + energy * 36;
      const glow = ctx.createRadialGradient(x, y, radius * .08, x, y, radius * 1.5);
      glow.addColorStop(0, `${color}44`); glow.addColorStop(.7, `${color}18`); glow.addColorStop(1, `${color}00`);
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(x, y, radius * 1.5, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
      if (theme.base === 'matrix') this.drawDotFace(ctx, x, y, radius, color, time, state);
      else this.drawSignal(ctx, x, y, radius, color, time, energy, state);
      ctx.restore();
      this.animation = requestAnimationFrame(frame);
    };
    this.animation = requestAnimationFrame(frame);
  }

  drawSignal(ctx, x, y, radius, color, time, energy, state) {
    const pulse = 1 + Math.sin(time / (state === 'speaking' ? 160 : 700)) * (.025 + energy * .045);
    const gradient = ctx.createRadialGradient(x - radius * .34, y - radius * .4, radius * .06, x, y, radius);
    gradient.addColorStop(0, '#ffffff44'); gradient.addColorStop(.3, `${color}88`); gradient.addColorStop(1, '#111821');
    ctx.fillStyle = gradient; ctx.beginPath(); ctx.arc(x, y, radius * pulse, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = `${color}88`; ctx.lineWidth = 1.5;
    for (let ring = 0; ring < 3; ring++) {
      const wave = (time / (state === 'speaking' ? 760 : 1800) + ring / 3) % 1;
      ctx.globalAlpha = (1 - wave) * (.18 + energy * .36);
      ctx.beginPath(); ctx.arc(x, y, radius * (.76 + wave * .48), 0, Math.PI * 2); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    const bars = 13;
    for (let i = 0; i < bars; i++) {
      const angle = (i / bars) * Math.PI * 2;
      const signal = state === 'speaking' || state === 'listening' ? Math.abs(Math.sin(time / 90 + i * 1.7)) : .25 + Math.abs(Math.sin(time / 450 + i)) * .2;
      const inner = radius * .42, outer = inner + 7 + signal * (state === 'speaking' ? 30 : 20);
      ctx.strokeStyle = color; ctx.globalAlpha = .36 + signal * .58; ctx.lineWidth = 2.2; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(x + Math.cos(angle) * inner, y + Math.sin(angle) * inner); ctx.lineTo(x + Math.cos(angle) * outer, y + Math.sin(angle) * outer); ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  drawDotFace(ctx, x, y, radius, color, time, state) {
    const step = radius * .14, half = 10;
    ctx.fillStyle = color;
    for (let row = -half; row <= half; row++) for (let col = -half; col <= half; col++) {
      const nx = col / half, ny = row / half;
      const face = nx * nx / .82 + (ny + .03) * (ny + .03) / 1.02 < 1;
      if (!face) continue;
      const eye = Math.abs(nx) > .27 && Math.abs(nx) < .57 && ny > -.26 && ny < -.08;
      const mouthRegion = Math.abs(nx) < .42 && ny > .28 && ny < .47;
      const nose = Math.abs(nx) < .09 && ny > -.05 && ny < .25;
      const modulation = state === 'speaking' && mouthRegion ? Math.abs(Math.sin(time / 110 + col * .4)) : state === 'listening' ? .45 + .35 * Math.sin(time / 600 - col * .2) : state === 'thinking' ? .45 + .25 * Math.sin(time / 900 - row * .25) : .45;
      const r = eye ? 2.25 : mouthRegion ? 1.7 + modulation * 1.3 : nose ? 1.8 : 1.35;
      ctx.globalAlpha = eye || (mouthRegion && state === 'speaking') ? .9 : .24 + Math.max(0, modulation) * .34;
      ctx.beginPath(); ctx.arc(x + col * step, y + row * step, r, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}

const voiceModeModule = new VoiceMode();
export default voiceModeModule;
