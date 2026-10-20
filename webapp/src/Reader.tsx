import { useEffect, useRef, useState } from 'react';
import {
  Button,
  IconTintColor,
  ListItem,
  Page,
  type PageHandle,
  ScrollView,
  TextColor,
  TextStyle,
  TextView,
  useBackNavigation,
} from '@wearables-ui-toolkit/mrbd';
import exclamation from '@wearables-ui-toolkit/icons/svg/exclamationmarks__outline.svg';
import phoneShield from '@wearables-ui-toolkit/icons/svg/phonebadgeshield__filled.svg';
import type { Approval } from './muse';

function useHeaderHeight() {
  const pageRef = useRef<PageHandle>(null);
  const [height, setHeight] = useState<number | undefined>(undefined);
  useEffect(() => { setHeight(pageRef.current?.getHeaderHeight() || undefined); }, []);
  return { pageRef, height };
}

/** Every pending approval with its details; deciding happens in the Muse phone app. */
export function ApprovalSheet({ approvals, onClose }: { approvals: Approval[]; onClose: () => void }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const { pageRef, height } = useHeaderHeight();
  useBackNavigation(() => { onClose(); return true; });
  // Focus the scroll area (not the button at the bottom) so the sheet opens at the top.
  useEffect(() => { rootRef.current?.querySelector<HTMLElement>('[tabindex]')?.focus({ preventScroll: true }); }, []);
  return (
    <Page ref={pageRef} headerText={approvals.length > 1 ? `${approvals.length} approvals waiting` : 'Approval waiting'}
          headerIcon={phoneShield} headerMetadata="Decide on your phone">
      <div ref={rootRef} className="muse-reader">
        <ScrollView insetForHeader headerHeight={height} tabIndex={0} scrollbarEnabled height="100%"
                    ariaLabel="Pending approvals">
          <div className="muse-reader-body">
            {approvals.map((a, i) => (
              <div key={i} className="muse-approval-detail">
                <TextView textStyle={TextStyle.HEADING2}>{a.action}</TextView>
                {a.details.map((d, j) => (
                  <ListItem key={j} icon={exclamation} iconTintColor={IconTintColor.WARNING} title={d}
                            focusable={false} />
                ))}
              </div>
            ))}
            <div className="muse-sheet-actions">
              <Button title="Got it" onClick={onClose} />
            </div>
          </div>
        </ScrollView>
      </div>
    </Page>
  );
}
