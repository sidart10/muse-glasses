import { Component, Fragment, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  IconTintColor,
  MaterialLibrary,
  InputTextView,
  InputTextViewSize,
  ListItem,
  Page,
  StatusIndicatorType,
  TextColor,
  TextStyle,
  TextView,
  VerticalList,
} from '@wearables-ui-toolkit/mrbd';
import aiStars from '@wearables-ui-toolkit/icons/svg/aistars3__outline.svg';
import exclamation from '@wearables-ui-toolkit/icons/svg/exclamationmarks__outline.svg';
import phoneShield from '@wearables-ui-toolkit/icons/svg/phonebadgeshield__filled.svg';
import { type Phase, registerVoiceTools, useMuse } from './muse';
import { ApprovalSheet } from './Reader';
import { Mascot, type Mood } from './Mascot';
import { MessageBubble } from './MessageBubble';

function ago(at: number): string {
  const m = Math.round((Date.now() - at) / 60_000);
  return m < 1 ? 'just now' : m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
}

const PHASE_LABEL: Record<Phase, string> = {
  idle: 'Ready', sending: 'Sending', working: 'Working', answering: 'Answering', done: 'Ready', error: 'Ready',
};

// Dictation lands as one block of text; it sends on its own after this pause, so there is
// no separate Send step. A pause mid-thought longer than this sends what's there.
const AUTO_SEND_MS = 2_000;

// The glasses only open their composer (dictation) when the wearer pinches a focused text field;
// an app cannot open it. So the message field is always on screen and is the default focus:
// one pinch, then talk.

// The approvals screen gets its own history entry: Back on the glasses (middle-finger pinch)
// calls history.back(), so it closes it. On the chat itself Back opens the system menu.
type Overlay = 'none' | 'approvals';

// A raised pill like the send button; the toolkit's text-input material reads as sunk in.
const INPUT_MATERIAL = MaterialLibrary.button();

export function MuseApp() {
  const { state, say, ask } = useMuse();
  const [overlay, setOverlay] = useState<Overlay>('none');
  const [draft, setDraft] = useState('');
  const [sendError, setSendError] = useState('');
  const [sending, setSending] = useState(false);
  const lastRef = useRef<HTMLDivElement>(null);
  const approvalRef = useRef<HTMLDivElement>(null);
  const turnRef = useRef<HTMLDivElement>(null); // your latest message, with the mascot beside it
  const composerRef = useRef<HTMLDivElement>(null);
  const autoSend = useRef<number | undefined>(undefined);
  const inFlight = useRef(false);
  const draftRef = useRef('');
  draftRef.current = draft;

  // "What did Beru say?" reads the latest answer; a ref keeps the tools registered once.
  const latest = useRef('');
  latest.current = state.replies.map((r) => r.text).join('\n\n') || state.error;
  useEffect(() => registerVoiceTools(ask, () => latest.current), [ask]);

  // ---- history-backed overlays -------------------------------------------------------------
  useEffect(() => {
    if (history.state?.view && history.state.view !== 'main') history.back(); // reload inside an overlay
    else history.replaceState({ view: 'main' }, '');
    const onPop = () => setOverlay('none');
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  const openOverlay = (next: Overlay) => { history.pushState({ view: next }, ''); setOverlay(next); };
  const closeOverlay = () => {
    const current = history.state?.view;
    setOverlay('none'); // unmount now, so a second tap can't call back() twice
    if (current && current !== 'main') history.back();
  };
  useEffect(() => {
    if (overlay === 'approvals' && !state.approvals.length) closeOverlay();
  }, [overlay, state.approvals.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- mascot ----------------------------------------------------------------------------------
  const [oneShot, setOneShot] = useState<Mood | null>('wave');
  const lastPhase = useRef(state.phase);
  useEffect(() => {
    const finished = lastPhase.current !== 'done' && state.phase === 'done' && !state.restoredAt;
    if (lastPhase.current !== state.phase) setOneShot(finished ? 'celebrate' : null);
    lastPhase.current = state.phase;
  }, [state.phase, state.restoredAt]);
  useEffect(() => {
    if (!oneShot) return;
    const t = window.setTimeout(() => setOneShot(null), 6_000);
    return () => window.clearTimeout(t);
  }, [oneShot]);
  const busy = state.phase === 'sending' || state.phase === 'working' || state.phase === 'answering';
  const mood: Mood = oneShot ?? (state.phase === 'sending' || state.phase === 'working' ? 'working'
    : state.phase === 'answering' ? 'making' : 'idle');

  // ---- keep the newest message in view, and focus on the message field ---------------------------
  // Focus returns to the field whenever the chat changes, unless the wearer is reading further up.
  const focusComposer = () => composerRef.current?.querySelector('textarea')?.focus({ preventScroll: true });
  // Swiping up from the message box goes to a waiting approval first, then the newest message.
  const focusLastMessage = () => {
    const focusable = (el: HTMLElement | null) => el?.querySelector<HTMLElement>('[tabindex]') ?? null;
    const lastMessage = [...document.querySelectorAll<HTMLElement>('.msg-row [tabindex]')].at(-1) ?? null;
    (focusable(approvalRef.current) ?? lastMessage)?.focus();
  };
  useLayoutEffect(() => {
    if (overlay !== 'none') return;
    const active = document.activeElement;
    const reading = active instanceof HTMLElement && active !== document.body
      && !composerRef.current?.contains(active) && !lastRef.current?.contains(active);
    if (reading) return;
    // Show the latest exchange from the mascot down; a short reply just settles at the bottom.
    (turnRef.current ?? lastRef.current)?.scrollIntoView({ block: 'start' });
    focusComposer();
    const t = window.setTimeout(focusComposer, 50); // after the toolkit's focus settles
    return () => window.clearTimeout(t);
  }, [state.thread.length, state.activity, state.phase, state.approvals.length, overlay, state.link]);

  const send = async (text: string) => {
    if (!text.trim() || inFlight.current) return;
    inFlight.current = true;
    window.clearTimeout(autoSend.current);
    setSending(true);
    setSendError('');
    try {
      await say(text);
      if (draftRef.current === text) setDraft(''); // keep anything added meanwhile
    } catch (err) {
      setSendError((err as Error).message); // the text stays in the field
    } finally {
      inFlight.current = false;
      setSending(false);
      // Dictation that landed while this was sending would otherwise sit unsent.
      const pending = draftRef.current;
      if (pending.trim() && pending !== text) {
        window.clearTimeout(autoSend.current);
        autoSend.current = window.setTimeout(() => { void send(pending); }, AUTO_SEND_MS);
      }
    }
  };
  const onDraft = (text: string) => {
    setDraft(text);
    window.clearTimeout(autoSend.current);
    if (text.trim()) autoSend.current = window.setTimeout(() => { void send(text); }, AUTO_SEND_MS);
  };
  useEffect(() => () => window.clearTimeout(autoSend.current), []);

  const [, tick] = useState(0);
  useEffect(() => {
    if (!state.restoredAt) return;
    const t = window.setInterval(() => tick((n) => n + 1), 60_000);
    return () => window.clearInterval(t);
  }, [state.restoredAt]);

  if (overlay === 'approvals' && state.approvals.length) {
    return <ApprovalSheet approvals={state.approvals} onClose={closeOverlay} />;
  }

  const indicator = !state.connected ? StatusIndicatorType.INACTIVE
    : state.approvals.length ? StatusIndicatorType.WARNING
    : busy ? StatusIndicatorType.ACTIVE : StatusIndicatorType.POSITIVE;
  const metadata = state.link === 'off' ? 'Switched off'
    : !state.reached ? 'Connecting…'
    : !state.connected ? 'Mac offline'
    : state.queued ? `${PHASE_LABEL[state.phase]} · ${state.queued} queued`
    : state.restoredAt ? `Last chat ${ago(state.restoredAt)}` : PHASE_LABEL[state.phase];
  const linked = state.link === 'linked' || state.link === 'off';
  const empty = !state.thread.length;
  const lastIndex = state.thread.length - 1;
  const lastIsMessage = !busy && !state.approvals.length;
  const lastYou = state.thread.map((m) => m.role).lastIndexOf('you');
  // Beru's mascot sits under your latest message, above its answer: working, answering, celebrating.
  const mascotRow = (
    <div ref={busy ? lastRef : undefined} className="mascot-row" aria-live="polite">
      <Mascot mood={mood} size={96} onEnded={() => setOneShot(null)} />
      {busy && (
        <TextView textStyle={TextStyle.BODY2} textColor={TextColor.SECONDARY}>
          {state.activity || (state.phase === 'answering' ? 'Beru is answering…' : 'Beru is thinking…')}
        </TextView>
      )}
    </div>
  );

  return (
    <Page headerText="Beru" headerIcon={aiStars} headerStatusIndicator={indicator} headerMetadata={metadata}>
      {!linked ? (
        <Centered title={state.link === 'expired' ? 'Link expired' : 'Not linked yet'}
                  body="Scan the install code on your Mac to connect." />
      ) : empty && !busy ? (
        <div className="muse-centered muse-empty">
          <Mascot mood={mood} size={200} onEnded={() => setOneShot(null)} />
          <TextView textStyle={TextStyle.HEADING1}>
            {state.link === 'off' ? 'Bridge switched off' : 'What can I do for you?'}
          </TextView>
        </div>
      ) : (
        <div className="thread-shell">
          <VerticalList insetForHeader scrollbarEnabled contentClassName="thread" ariaLabel="Chat with Beru">
            {state.thread.map((msg, i) => (
              <Fragment key={msg.id}>
                <MessageBubble msg={msg} last={i === lastIndex && lastIsMessage}
                               ref={i === lastYou ? turnRef : i === lastIndex && lastIsMessage ? lastRef : undefined} />
                {i === lastYou && mascotRow}
              </Fragment>
            ))}
            {lastYou < 0 && busy && mascotRow}
            {/* Muse usually asks for approval mid-task, so the card shows while Beru is still working. */}
            {state.approvals[0] && (
              <div ref={approvalRef}>
                <ListItem
                  icon={phoneShield}
                  iconTintColor={IconTintColor.WARNING}
                  title={`Approve on phone: ${state.approvals[0].action}`}
                  subtitle={[...state.approvals[0].details,
                             ...(state.approvals.length > 1 ? [`${state.approvals.length - 1} more waiting`] : [])]
                    .join(' · ') || 'Open the Muse app on your phone'}
                  subtitleMaxLines={1}
                  secondaryIcon={exclamation}
                  secondaryIconTintColor={IconTintColor.WARNING}
                  onClick={() => openOverlay('approvals')}
                />
              </div>
            )}
          </VerticalList>
        </div>
      )}

      {linked && (
        <div className="muse-dock">
          {sendError && (
            <TextView className="muse-send-error" textStyle={TextStyle.META1} textColor={TextColor.ACCENT}>
              {sendError}
            </TextView>
          )}
          <div ref={composerRef}>
            <InputTextView size={InputTextViewSize.FULL} material={INPUT_MATERIAL} hint="Pinch and talk to Beru"
                           text={draft} onTextChange={onDraft} showLoader={sending} loadingLabel="Sending"
                           actionLabel="Send to Beru" onSend={(text) => { void send(text); }}
                           inputProps={{
                             'aria-label': 'Message to Beru',
                             onKeyDown: (e) => {
                               // Swipe up from the field moves into the chat to read earlier messages.
                               const el = e.currentTarget;
                               if (e.key === 'ArrowUp' && el.selectionStart === 0 && el.selectionEnd === 0) {
                                 e.preventDefault();
                                 focusLastMessage();
                               }
                             },
                           }} />
          </div>
        </div>
      )}
    </Page>
  );
}

function Centered({ title, body }: { title: string; body?: string }) {
  return (
    <div className="muse-centered muse-empty">
      <TextView textStyle={TextStyle.HEADING1}>{title}</TextView>
      {body && <TextView textStyle={TextStyle.BODY2} textColor={TextColor.SECONDARY}>{body}</TextView>}
    </div>
  );
}

/** One rendering failure must not leave a blank lens. */
export class SafeBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) { console.error('Beru app crashed', error); }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <Page headerText="Beru" headerIcon={aiStars}>
        <Centered title="Something went wrong" body="Close and reopen Beru." />
      </Page>
    );
  }
}
