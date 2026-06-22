'use client';

import { useRef } from 'react';
import CoachReportPanel from '@/components/CoachReportPanel';
import CoachChat, { CoachChatHandle } from '@/components/CoachChat';

interface Props {
  activityId: string;
  hasMetrics: boolean;
}

// Bridges the coach report and the coach chat: the report's conversational
// question options (reply/dispute/custom) start the chat below via an imperative
// handle, so the two siblings coordinate without lifting the chat's state up.
// The chat only exists when the activity has metrics (matching the previous
// page-level gate), so the report's chips fall back to non-interactive when it
// is absent.
export default function CoachSection({ activityId, hasMetrics }: Props) {
  const chatRef = useRef<CoachChatHandle>(null);

  return (
    <div className="space-y-6">
      <CoachReportPanel
        activityId={activityId}
        hasMetrics={hasMetrics}
        onStartChat={hasMetrics ? (text) => chatRef.current?.ask(text) : undefined}
      />
      {hasMetrics && <CoachChat ref={chatRef} activityId={activityId} />}
    </div>
  );
}
