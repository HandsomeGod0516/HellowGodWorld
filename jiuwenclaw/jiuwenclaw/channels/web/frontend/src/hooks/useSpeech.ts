/**
 * 語音輸入輸出 Hook
 *
 * 使用 Web Speech API 實現語音識別（STT）和語音合成（TTS）
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import i18n from '../i18n';

// ============================================================================
// 語音識別 (STT)
// ============================================================================

interface UseSpeechRecognitionOptions {
  language?: string;
  continuous?: boolean;
  interimResults?: boolean;
  /** 無聲音後多少毫秒結束識別，預設 5000。需配合 continuous: true 使用。 */
  silenceTimeoutMs?: number;
  /** 返回 true 時，onend 後會自動重啟識別。 */
  restartWhen?: () => boolean;
  onResult?: (transcript: string, isFinal: boolean) => void;
  onError?: (error: string) => void;
  onEnd?: () => void;
}

interface UseSpeechRecognitionReturn {
  isListening: boolean;
  transcript: string;
  interimTranscript: string;
  startListening: () => void;
  stopListening: () => void;
  isSupported: boolean;
}

// Web Speech API 型別（部分瀏覽器/TS 未內建）
interface SpeechRecognitionEventMap {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}
interface SpeechRecognitionInstance extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onresult: ((event: SpeechRecognitionEventMap) => void) | null;
}
interface SpeechRecognitionConstructor {
  new (): SpeechRecognitionInstance;
}

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

// 供本檔案內 ref 等使用
type SpeechRecognition = SpeechRecognitionInstance;

export function useSpeechRecognition(
  options: UseSpeechRecognitionOptions = {}
): UseSpeechRecognitionReturn {
  const {
    language = 'cmn-Hans-CN', // 普通話簡體中文（比 zh-CN 更準確）
    continuous = false, // 預設檢測到停止說話後自動結束
    interimResults = true,
    silenceTimeoutMs = 5000, // 無聲音後 5s 結束（需配合 continuous: true）
    restartWhen,
    onResult,
    onError,
    onEnd,
  } = options;

  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [interimTranscript, setInterimTranscript] = useState('');
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const manualStopRef = useRef(false);
  const autoStopRef = useRef(false);
  const useContinuousRef = useRef(false);

  // 檢查瀏覽器支援
  const isSupported =
    typeof window !== 'undefined' &&
    ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window);

  const clearSilenceTimer = useCallback(() => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
  }, []);

  const scheduleSilenceStop = useCallback(() => {
    if (silenceTimeoutMs <= 0) {
      return;
    }
    clearSilenceTimer();
    silenceTimerRef.current = setTimeout(() => {
      silenceTimerRef.current = null;
      autoStopRef.current = true;
      recognitionRef.current?.stop();
    }, silenceTimeoutMs);
  }, [clearSilenceTimer, silenceTimeoutMs]);

  const startListening = useCallback(() => {
    if (!isSupported) {
      onError?.(i18n.t('speech.recognitionUnsupported'));
      return;
    }

    clearSilenceTimer();
    manualStopRef.current = false;
    autoStopRef.current = false;

    // 建立識別例項
    const SpeechRecognitionCtor =
      window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognitionCtor) {
      onError?.(i18n.t('speech.recognitionUnsupported'));
      return;
    }
    const recognition = new SpeechRecognitionCtor();

    // 使用自定義靜默超時時，用 continuous=true 避免瀏覽器約 2s 就結束
    const useContinuous = continuous || silenceTimeoutMs > 0;
    useContinuousRef.current = useContinuous;
    recognition.lang = language;
    recognition.continuous = useContinuous;
    recognition.interimResults = interimResults;

    recognition.onstart = () => {
      setIsListening(true);
      setTranscript('');
      setInterimTranscript('');
      scheduleSilenceStop();
    };

    recognition.onresult = (event) => {
      let finalTranscript = '';
      let interim = '';

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          finalTranscript += result[0].transcript;
        } else {
          interim += result[0].transcript;
        }
      }

      if (finalTranscript) {
        setTranscript((prev) => prev + finalTranscript);
        onResult?.(finalTranscript, true);
        scheduleSilenceStop();
      }

      setInterimTranscript(interim);
      if (interim) {
        onResult?.(interim, false);
        scheduleSilenceStop();
      }
    };

    recognition.onerror = (event) => {
      console.error('Speech recognition error:', event.error);
      clearSilenceTimer();
      setIsListening(false);
      
      const errorMessages: Record<string, string> = {
        'no-speech': i18n.t('speech.errors.noSpeech'),
        'audio-capture': i18n.t('speech.errors.noMic'),
        'not-allowed': i18n.t('speech.errors.notAllowed'),
        'network': i18n.t('speech.errors.network'),
      };
      
      onError?.(errorMessages[event.error] || i18n.t('speech.errors.recognitionGeneric', { error: event.error }));
    };

    recognition.onend = () => {
      clearSilenceTimer();
      if (manualStopRef.current) {
        manualStopRef.current = false;
        setIsListening(false);
        onEnd?.();
        return;
      }
      if (autoStopRef.current) {
        autoStopRef.current = false;
        setIsListening(false);
        onEnd?.();
        return;
      }
      if (useContinuousRef.current && restartWhen?.()) {
        try {
          recognitionRef.current?.start();
          return;
        } catch (error) {
          console.warn('Speech recognition restart failed:', error);
        }
      }
      setIsListening(false);
      onEnd?.();
    };

    recognitionRef.current = recognition;
    recognition.start();
  }, [isSupported, language, continuous, interimResults, silenceTimeoutMs, restartWhen, onResult, onError, onEnd, clearSilenceTimer]);

  const stopListening = useCallback(() => {
    clearSilenceTimer();
    if (recognitionRef.current) {
      manualStopRef.current = true;
      recognitionRef.current.stop();
      recognitionRef.current = null;
    }
    setIsListening(false);
  }, [clearSilenceTimer]);

  // 元件解除安裝時清理
  useEffect(() => {
    return () => {
      clearSilenceTimer();
      if (recognitionRef.current) {
        recognitionRef.current.stop();
      }
    };
  }, [clearSilenceTimer]);

  return {
    isListening,
    transcript,
    interimTranscript,
    startListening,
    stopListening,
    isSupported,
  };
}

// ============================================================================
// 語音合成 (TTS)
// ============================================================================

interface UseSpeechSynthesisOptions {
  language?: string;
  rate?: number;
  pitch?: number;
  volume?: number;
  onStart?: () => void;
  onEnd?: () => void;
  onError?: (error: string) => void;
}

interface UseSpeechSynthesisReturn {
  isSpeaking: boolean;
  speak: (text: string) => void;
  stop: () => void;
  pause: () => void;
  resume: () => void;
  isSupported: boolean;
  voices: SpeechSynthesisVoice[];
}

export function useSpeechSynthesis(
  options: UseSpeechSynthesisOptions = {}
): UseSpeechSynthesisReturn {
  const {
    language = 'zh-CN',
    rate = 1,
    pitch = 1,
    volume = 1,
    onStart,
    onEnd,
    onError,
  } = options;

  const [isSpeaking, setIsSpeaking] = useState(false);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);

  // 檢查瀏覽器支援
  const isSupported =
    typeof window !== 'undefined' && 'speechSynthesis' in window;

  // 載入可用語音
  useEffect(() => {
    if (!isSupported) return;

    const loadVoices = () => {
      const availableVoices = window.speechSynthesis.getVoices();
      setVoices(availableVoices);
    };

    loadVoices();
    window.speechSynthesis.onvoiceschanged = loadVoices;

    return () => {
      window.speechSynthesis.onvoiceschanged = null;
    };
  }, [isSupported]);

  const speak = useCallback(
    (text: string) => {
      if (!isSupported) {
        onError?.(i18n.t('speech.synthesisUnsupported'));
        return;
      }

      // 停止當前播放
      window.speechSynthesis.cancel();

      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = language;
      utterance.rate = rate;
      utterance.pitch = pitch;
      utterance.volume = volume;

      // 選擇合適的中文語音
      const chineseVoice = voices.find(
        (v) => v.lang.includes('zh') || v.lang.includes('CN')
      );
      if (chineseVoice) {
        utterance.voice = chineseVoice;
      }

      utterance.onstart = () => {
        setIsSpeaking(true);
        onStart?.();
      };

      utterance.onend = () => {
        setIsSpeaking(false);
        onEnd?.();
      };

      utterance.onerror = (event) => {
        console.error('Speech synthesis error:', event);
        setIsSpeaking(false);
        onError?.(i18n.t('speech.errors.synthesisGeneric', { error: event.error }));
      };

      utteranceRef.current = utterance;
      window.speechSynthesis.speak(utterance);
    },
    [isSupported, language, rate, pitch, volume, voices, onStart, onEnd, onError]
  );

  const stop = useCallback(() => {
    if (isSupported) {
      window.speechSynthesis.cancel();
      setIsSpeaking(false);
    }
  }, [isSupported]);

  const pause = useCallback(() => {
    if (isSupported) {
      window.speechSynthesis.pause();
    }
  }, [isSupported]);

  const resume = useCallback(() => {
    if (isSupported) {
      window.speechSynthesis.resume();
    }
  }, [isSupported]);

  // 元件解除安裝時清理
  useEffect(() => {
    return () => {
      if (isSupported) {
        window.speechSynthesis.cancel();
      }
    };
  }, [isSupported]);

  return {
    isSpeaking,
    speak,
    stop,
    pause,
    resume,
    isSupported,
    voices,
  };
}
