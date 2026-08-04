'use client';

import CoachReportPanel from '@/components/CoachReportPanel';
import { useCoachSheet } from '@/components/coach/CoachSheetContext';

interface Props {
  activityId: string;
  hasMetrics: boolean;
}

// The activity page's coach surface: the report, and one way to talk about it.
// Tapping a conversational question option opens the coach sheet — which lands
// in this activity's own thread and carries the report at its head — with the
// question in the composer (#770, ADR 0027). The page had its own chat box until
// the thread surface shipped; two boxes over one relationship meant a runner who
// asked in the wrong one met a coach that had not heard the other. The options
// stay live whether or not the activity has metrics, since the thread no longer
// depends on this page having a chat to start.
export default function CoachSection({ activityId, hasMetrics }: Props) {
  const { enabled, openWith } = useCoachSheet();

  return (
    <div className="space-y-6">
      <CoachReportPanel
        activityId={activityId}
        hasMetrics={hasMetrics}
        // #784: with the thread surface switched off there is nothing to open,
        // so the options fall back to non-interactive chips (the panel's own
        // undefined-handler path) rather than offering a tap that goes nowhere.
        onStartChat={enabled ? text => openWith(text) : undefined}
      />
    </div>
  );
}
