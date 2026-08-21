/**
 * StatusBar 元件
 *
 * 狀態列，顯示當前模式、處理狀態、暫停/恢復按鈕
 * 採用 JiuwenClaw 風格
 */

import { useTranslation } from 'react-i18next';
import { useChatStore } from '../../stores';
import './StatusBar.css';

interface StatusBarProps {
  onPause?: () => void;
  onCancel?: () => void;
  onResume?: () => void;
}

export function StatusBar({ onPause, onCancel, onResume }: StatusBarProps) {
  const { t } = useTranslation();
  const { isProcessing, isPaused, pausedTask, interruptResult, switchingMode } = useChatStore();
  const showExec = (isProcessing || isPaused) && !switchingMode;
  /** 有中斷結果文案時，統一隻顯示居中的橫條（任務已暫停/恢復/取消/切換/已中斷） */
  const showInterruptBarOnly = Boolean(interruptResult?.message);

  return (
    <div className="statusbar-root">
      <div className="statusbar-center">
        {showInterruptBarOnly ? (
          <div
            className={`pill animate-fade-in ${
              interruptResult!.success
                ? 'bg-info text-white border-info'
                : 'bg-danger text-white border-danger'
            }`}
          >
            <span className="text-sm">{interruptResult!.message}</span>
          </div>
        ) : (
          <>
        {/* 執行狀態：左側取消，中間狀態，右側暫停/恢復 */}
        {showExec && (
          <div className="statusbar-exec">
            {onCancel && (
              <button
                onClick={onCancel}
                className="statusbar-action-btn statusbar-action-btn--cancel"
              >
                {t('statusBar.cancel')}
              </button>
            )}

            <div className={`statusbar-pill ${isPaused ? 'statusbar-pill--paused' : 'statusbar-pill--processing'}`}>
              <span className={`statusbar-dot ${isPaused ? '' : 'statusbar-dot--pulse'}`.trim()} />
              <span>
                {isPaused
                  ? pausedTask
                    ? t('statusBar.pausedWithTask', { task: pausedTask.slice(0, 20) })
                    : t('statusBar.paused')
                  : t('statusBar.processing')}
              </span>
            </div>

            {isPaused ? (
              onResume && (
                <button
                  onClick={onResume}
                  className="statusbar-action-btn statusbar-action-btn--resume"
                >
                  {t('statusBar.resume')}
                </button>
              )
            ) : (
              onPause && (
              <button
                onClick={onPause}
                className="statusbar-action-btn statusbar-action-btn--pause"
              >
                {t('statusBar.pause')}
              </button>
              )
            )}
          </div>
        )}
          </>
        )}
      </div>
    </div>
  );
}
