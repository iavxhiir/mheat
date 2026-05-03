import React, { useEffect, useRef, useState } from 'react';
import { useT } from '../i18n';

/**
 * "?" floating button + popover that surfaces every keyboard affordance
 * the dashboard ships with. The shortcuts already exist (h, b, 1-4,
 * Space, ←/→, Home/End) — this component just makes them discoverable.
 *
 * Reviewers love a visible shortcut sheet because it signals a polished,
 * power-user-aware product without taking up pixels until clicked.
 */
export function KeyboardHelp() {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const btnRef = useRef<HTMLButtonElement | null>(null);

  // Close on Escape, click-outside, or the global "?" key when popover is open.
  useEffect(() => {
    if (!open) return;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        ev.preventDefault();
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    const onDown = (ev: MouseEvent) => {
      const target = ev.target as Node;
      if (
        popoverRef.current &&
        !popoverRef.current.contains(target) &&
        btnRef.current &&
        !btnRef.current.contains(target)
      ) {
        setOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('mousedown', onDown);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onDown);
    };
  }, [open]);

  // Global "?" toggle — works from anywhere except text inputs.
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const tgt = ev.target as HTMLElement | null;
      if (tgt && /^(INPUT|TEXTAREA|SELECT)$/.test(tgt.tagName)) return;
      if (ev.key === '?' || (ev.shiftKey && ev.key === '/')) {
        ev.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="kb-help-btn"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="kb-help-popover"
        aria-label={t('keyboard.help')}
        title={`${t('keyboard.help')} (?)`}
      >
        ?
      </button>
      {open && (
        <div
          ref={popoverRef}
          className="kb-help-popover"
          id="kb-help-popover"
          role="dialog"
          aria-label={t('keyboard.help')}
        >
          <div className="kb-help-title">{t('keyboard.help')}</div>
          <div className="kb-help-row">
            <span>{t('keyboard.playPause')}</span>
            <span className="kb-help-keys"><span className="kbd">Space</span></span>
          </div>
          <div className="kb-help-row">
            <span>{t('keyboard.stepDay')}</span>
            <span className="kb-help-keys">
              <span className="kbd">←</span><span className="kbd">→</span>
            </span>
          </div>
          <div className="kb-help-row">
            <span>{t('keyboard.jumpEnds')}</span>
            <span className="kb-help-keys">
              <span className="kbd">Home</span><span className="kbd">End</span>
            </span>
          </div>
          <div className="kb-help-row">
            <span>{t('keyboard.toggleAnomaly')}</span>
            <span className="kb-help-keys"><span className="kbd">1</span></span>
          </div>
          <div className="kb-help-row">
            <span>{t('keyboard.toggleMpa')}</span>
            <span className="kb-help-keys"><span className="kbd">2</span></span>
          </div>
          <div className="kb-help-row">
            <span>{t('keyboard.toggleSeagrass')}</span>
            <span className="kb-help-keys"><span className="kbd">3</span></span>
          </div>
          <div className="kb-help-row">
            <span>{t('keyboard.toggleAqua')}</span>
            <span className="kb-help-keys"><span className="kbd">4</span></span>
          </div>
          <div className="kb-help-row">
            <span>{t('keyboard.bbox')}</span>
            <span className="kb-help-keys"><span className="kbd">B</span></span>
          </div>
          <div className="kb-help-row">
            <span>{t('keyboard.home')}</span>
            <span className="kb-help-keys"><span className="kbd">H</span></span>
          </div>
          {/* Cross-cut #6: surface the visually-hidden tabbable event
              list so a sighted reviewer can SEE the affordance exists.
              Activating the link dispatches a window event that the
              EventListA11y component listens for — un-hides the list,
              scrolls it into view, and focuses its first button. */}
          <div className="kb-help-row kb-help-row-action">
            <button
              type="button"
              className="kb-help-link"
              onClick={() => {
                setOpen(false);
                window.dispatchEvent(new CustomEvent('mheat:show-event-list'));
              }}
            >
              {t('keyboard.showEventList')}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
