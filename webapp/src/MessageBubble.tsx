import { forwardRef, useMemo } from 'react';
import {
  Container,
  CornerRadius,
  MaterialLibrary,
  TailShapeProvider,
  TextColor,
  TextStyle,
  TextView,
} from '@wearables-ui-toolkit/mrbd';
import type { Msg } from './muse';

/**
 * One chat message. Yours sit in the Display's outbound message bubble on the right; Beru's are
 * plain text on the lens (transparent), lit only by the focus treatment when selected.
 */
export const MessageBubble = forwardRef<HTMLDivElement, { msg: Msg; last: boolean }>(
  function MessageBubble({ msg, last }, ref) {
    const outgoing = msg.role === 'you';
    const shape = useMemo(() => (outgoing ? new TailShapeProvider('right', CornerRadius.MEDIUM) : undefined), [outgoing]);
    const material = useMemo(() => (outgoing ? MaterialLibrary.outboundMessage() : MaterialLibrary.default()), [outgoing]);
    const who = outgoing ? 'You' : 'Beru';
    return (
      <div ref={ref} className={`msg-row ${outgoing ? 'msg-row--you' : 'msg-row--beru'}`}>
        <Container
          className="msg-bubble"
          focusable
          initialFocusEligible={last}
          ariaLabel={`${who}: ${msg.text}`}
          material={material}
          shapeProvider={shape}
        >
          <div className={`msg-content msg-content--${outgoing ? 'right' : 'plain'}`}>
            <TextView as="p" textStyle={TextStyle.BODY2}
                      textColor={msg.role === 'error' ? TextColor.ACCENT : TextColor.PRIMARY}>
              {msg.text}
            </TextView>
          </div>
        </Container>
      </div>
    );
  },
);
