// static/js/voiceRecorder.js

/**
 * Voice recording with optional Speech-to-Text transcription.
 *
 * STT providers:
 *   "disabled"       — record audio as file attachment (original behavior)
 *   "browser"        — use Web Speech API for real-time transcription
 *   "local"          — send recording to server /api/stt/transcribe (Whisper)
 *   "endpoint:<id>"  — send recording to server /api/stt/transcribe (API)
 */

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let recordingStartTime = null;
let recordingInterval = null;

// Browser STT state
let _recognition = null;
let _browserTranscript = '';
let _browserSttError = '';
let _recognitionEndPromise = null;
let _resolveRecognitionEnd = null;

// Cached STT provider — refreshed on settings change
let _sttProvider = 'disabled';

function getRecordingMimeType() {
  if (typeof MediaRecorder === 'undefined') return '';
  if (typeof MediaRecorder.isTypeSupported !== 'function') return '';
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/mp4',
    'audio/webm',
    'audio/ogg;codecs=opus',
  ];
  return candidates.find(type => MediaRecorder.isTypeSupported?.(type)) || '';
}

function getAudioExtension(mimeType) {
  if (mimeType.includes('mp4') || mimeType.includes('aac')) return 'mp4';
  if (mimeType.includes('ogg')) return 'ogg';
  return 'webm';
}

function _dispatchSTTState(state) {
  window.dispatchEvent(new CustomEvent('odysseus-stt-state', { detail: { state } }));
}

/**
 * Fetch current STT provider from server settings
 */
async function refreshSttProvider() {
  try {
    const res = await fetch('/api/stt/stats', { credentials: 'same-origin' });
    if (res.ok) {
      const stats = await res.json();
      _sttProvider = stats.provider || 'disabled';
      // Notify the send button to update its icon
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    }
  } catch (e) {
    console.warn('Failed to fetch STT stats:', e);
  }
}

/**
 * Format seconds as MM:SS
 */
function formatTime(seconds) {
  const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
  const secs = (seconds % 60).toString().padStart(2, '0');
  return `${mins}:${secs}`;
}

/**
 * Reset UI state after recording ends
 */
function _resetRecordingUI() {
  isRecording = false;
  if (recordingInterval) {
    clearInterval(recordingInterval);
    recordingInterval = null;
  }
  // Reset send button via global callback
  const sendBtn = document.querySelector('.send-btn');
  if (sendBtn) {
    sendBtn.classList.remove('recording');
    sendBtn.dataset.mode = '';
  }
  if (window._updateSendBtnIcon) {
    setTimeout(window._updateSendBtnIcon, 50);
  }
}

/**
 * Start browser speech recognition alongside recording
 */
function startBrowserSTT() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) throw new Error('Browser speech recognition is not supported here. Select Local Whisper or a transcription endpoint in Settings.');

  _browserTranscript = '';
  _browserSttError = '';
  _recognition = new SpeechRecognition();
  _recognition.continuous = true;
  _recognition.interimResults = false;
  _recognition.lang = navigator.language || 'en-US';
  _recognitionEndPromise = new Promise(resolve => { _resolveRecognitionEnd = resolve; });

  _recognition.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (event.results[i].isFinal) {
        _browserTranscript += event.results[i][0].transcript + ' ';
      }
    }
  };

  _recognition.onerror = (e) => {
    _browserSttError = e.error || 'unknown';
    console.warn('Browser STT error:', e.error);
  };

  _recognition.onend = () => {
    if (_resolveRecognitionEnd) _resolveRecognitionEnd();
    _resolveRecognitionEnd = null;
  };

  _recognition.start();
}

async function stopBrowserSTT() {
  const recognition = _recognition;
  if (!recognition) return _browserTranscript.trim();

  // stop() can still produce a final result asynchronously. Wait for `end`
  // before reading the transcript, with a timeout for browser implementations
  // that fail to send the lifecycle event.
  try { recognition.stop(); } catch (e) { /* already stopped */ }
  if (_recognitionEndPromise) {
    await Promise.race([
      _recognitionEndPromise,
      new Promise(resolve => setTimeout(resolve, 1800)),
    ]);
  }
  if (_recognition === recognition) _recognition = null;
  _recognitionEndPromise = null;
  _resolveRecognitionEnd = null;
  return _browserTranscript.trim();
}

/**
 * Send audio to server for transcription
 */
async function transcribeOnServer(audioBlob, filename) {
  const formData = new FormData();
  formData.append('file', audioBlob, filename);

  const res = await fetch('/api/stt/transcribe', {
    method: 'POST',
    credentials: 'same-origin',
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.message || 'Transcription failed');
  }

  const data = await res.json();
  return data.text || '';
}

/**
 * Insert transcribed text into the chat input
 */
function insertTranscription(text, showToast) {
  if (!text) return;
  const input = document.getElementById('message');
  if (!input) return;

  const existing = input.value.trim();
  input.value = existing ? existing + ' ' + text : text;

  // Trigger auto-resize and icon update
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();

  if (showToast) showToast('Transcribed');
}

/**
 * Start voice recording
 */
export function startRecording(onFileCreated, showToast, showError, onTranscription = null) {
  // Check for secure context (getUserMedia requires HTTPS or localhost)
  if (!window.isSecureContext) {
    if (showError) showError('Microphone requires HTTPS. Use a reverse proxy with SSL or access via localhost.');
    _dispatchSTTState('error');
    _resetRecordingUI();
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (showError) showError('Microphone not supported in this browser.');
    _dispatchSTTState('error');
    _resetRecordingUI();
    return;
  }

  audioChunks = [];

  const mimeType = getRecordingMimeType();
  if (typeof MediaRecorder === 'undefined') {
    if (showError) showError('Audio recording is not supported by this browser.');
    _dispatchSTTState('error');
    _resetRecordingUI();
    return;
  }

  if (_sttProvider === 'browser' && !(window.SpeechRecognition || window.webkitSpeechRecognition)) {
    if (showError) showError('This browser can record audio but cannot transcribe it directly. Select Local Whisper or a transcription endpoint in Settings.');
    _dispatchSTTState('error');
    _resetRecordingUI();
    return;
  }

  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(stream => {
      mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);

      mediaRecorder.ondataavailable = event => {
        if (event.data.size > 0) {
          audioChunks.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        _dispatchSTTState('processing');

        const recordedType = mediaRecorder.mimeType || mimeType || audioChunks[0]?.type || 'audio/webm';
        const audioBlob = new Blob(audioChunks, { type: recordedType });
        const extension = getAudioExtension(recordedType);
        const filename = `voice-message-${Date.now()}.${extension}`;
        const provider = _sttProvider;
        // Let browser recognition flush its final result before closing the
        // microphone stream. Some browsers deliver that result after stop().
        const browserTranscript = provider === 'browser' ? await stopBrowserSTT() : '';
        stream.getTracks().forEach(track => track.stop());

        if (provider === 'browser') {
          const transcript = browserTranscript;
          if (transcript) {
            insertTranscription(transcript, showToast);
            if (onTranscription) onTranscription(transcript);
          } else {
            if (_browserSttError) {
              if (showError) showError(`Browser transcription failed (${_browserSttError}). Try Local Whisper or a transcription endpoint in Settings.`);
            } else if (showToast) {
              showToast('No speech detected');
            }
            const audioFile = new File([audioBlob], filename, { type: recordedType });
            if (onFileCreated) onFileCreated(audioFile);
          }
        } else if (provider === 'local' || provider.startsWith('endpoint:')) {
          // Show "Transcribing..." feedback
          if (showToast) showToast('Transcribing...', 5000);
          try {
            const transcript = await transcribeOnServer(audioBlob, filename);
            if (transcript) {
              insertTranscription(transcript, showToast);
              if (onTranscription) onTranscription(transcript);
            } else {
              if (showToast) showToast('No speech detected');
            }
          } catch (e) {
            console.error('STT transcription error:', e);
            if (showError) showError('Transcription failed: ' + e.message);
            // Fallback: attach as file
            const audioFile = new File([audioBlob], filename, { type: recordedType });
            if (onFileCreated) onFileCreated(audioFile);
          }
        } else {
          // STT disabled — attach audio file
          const audioFile = new File([audioBlob], filename, { type: recordedType });
          if (onFileCreated) onFileCreated(audioFile);
        }

        _dispatchSTTState('ready');
        _resetRecordingUI();
      };

      mediaRecorder.start();
      isRecording = true;
      recordingStartTime = new Date();
      _dispatchSTTState('listening');

      // Start browser STT if that's the provider
      if (_sttProvider === 'browser') {
        startBrowserSTT();
      }

      if (showToast) {
        showToast('Recording...');
      }
    })
    .catch(error => {
      console.error('Microphone access error:', error);
      _dispatchSTTState('error');
      if (showError) {
        if (error.name === 'NotAllowedError') {
          showError('Microphone access denied. Check browser permissions.');
        } else if (error.name === 'NotFoundError') {
          showError('No microphone found.');
        } else {
          showError('Microphone error: ' + error.message);
        }
      }
      _resetRecordingUI();
    });
}

/**
 * Stop voice recording
 */
export function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    // isRecording will be set to false in _resetRecordingUI called from onstop
  } else {
    _resetRecordingUI();
  }
}

/**
 * Check if currently recording
 */
export function getIsRecording() {
  return isRecording;
}

/**
 * Initialize recording state
 */
export function init() {
  isRecording = false;
  refreshSttProvider();
}

const voiceRecorderModule = {
  startRecording,
  stopRecording,
  getIsRecording,
  init,
  refreshSttProvider,
  get _sttProvider() { return _sttProvider; },
  set _sttProvider(v) { _sttProvider = v; },
};

export default voiceRecorderModule;
