'use client';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';

interface CheckInData {
  rpe: number;
  pain_score: number;
  notes?: string | null;
}

interface Props {
  activityId: string;
  existingCheckIn?: CheckInData | null;
}

export default function CheckInForm({ activityId, existingCheckIn }: Props) {
  const router = useRouter();
  
  // Mode state: If data exists, start in View Mode (isEditing = false)
  const [isEditing, setIsEditing] = useState(!existingCheckIn);
  
  // Form State
  const [pain, setPain] = useState<number | ''>(existingCheckIn?.pain_score ?? '');
  const [effort, setEffort] = useState<number | ''>(existingCheckIn?.rpe ?? '');
  const [notes, setNotes] = useState(existingCheckIn?.notes ?? '');
  const [submitting, setSubmitting] = useState(false);

  // Sync state with props when server data updates (e.g. after router.refresh)
  useEffect(() => {
    if (existingCheckIn) {
      setPain(existingCheckIn.pain_score);
      setEffort(existingCheckIn.rpe);
      setNotes(existingCheckIn.notes || '');
      // If we receive new data, we might want to ensure we are in view mode, 
      // unless the user is actively editing. For simplicity, we won't force-close edit mode here
      // to avoid interrupting typing, but we ensure the *values* are fresh if they were null.
      if (!isEditing) {
          setIsEditing(false);
      }
    }
  }, [existingCheckIn]); // Removed isEditing dependency to avoid loop
  
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    
    try {
        const payload = {
            rpe: effort === '' ? null : Number(effort),
            pain_score: pain === '' ? null : Number(pain),
            notes: notes || null
        };
        
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'}/api/activities/${activityId}/checkin`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (!res.ok) throw new Error("Failed to save check-in");
        
        // Optimistic update / switch to view
        setIsEditing(false);
        router.refresh(); 
    } catch (err) {
        console.error(err);
        alert("Error saving check-in");
    } finally {
        setSubmitting(false);
    }
  }

  // --- VIEW MODE ---
  if (!isEditing && existingCheckIn) {
    return (
      <div className="bg-green-50 rounded-lg shadow-sm border border-green-200 p-4">
          <div className="flex justify-between items-start mb-3">
              <h3 className="font-semibold text-green-800">Check-In Completed</h3>
              <button 
                onClick={() => setIsEditing(true)}
                className="text-xs font-medium text-green-700 hover:text-green-900 underline"
              >
                Edit
              </button>
          </div>
          <dl className="space-y-2 text-sm text-green-700">
              <div className="flex justify-between border-b border-green-100 pb-1">
                  <dt className="text-green-800 opacity-80">RPE</dt>
                  <dd className="font-medium">{existingCheckIn.rpe}/10</dd>
              </div>
              <div className="flex justify-between border-b border-green-100 pb-1">
                  <dt className="text-green-800 opacity-80">Pain</dt>
                  <dd className="font-medium">{existingCheckIn.pain_score}/10</dd>
              </div>
              {existingCheckIn.notes && (
                  <div className="pt-2">
                       <dt className="text-xs font-bold uppercase tracking-wider text-green-800 opacity-60 mb-1">Notes</dt>
                       <dd className="italic bg-white/50 p-2 rounded border border-green-100/50 text-green-900">
                        "{existingCheckIn.notes}"
                      </dd>
                  </div>
              )}
          </dl>
      </div>
    );
  }

  // --- EDIT MODE ---
  return (
    <form onSubmit={handleSubmit} className="space-y-4 bg-gray-50 p-4 rounded-lg border border-gray-100">
      <h3 className="font-semibold text-gray-800">
        {existingCheckIn ? 'Edit Check-In' : 'Quick Check-In'}
      </h3>
      
      <div>
        <label className="block text-sm font-medium mb-1 text-gray-700">RPE (1-10 Effort)</label>
        <input 
          type="number" min="1" max="10"
          value={effort}
          onChange={(e) => setEffort(e.target.value === '' ? '' : Number(e.target.value))}
          className="w-full border border-gray-300 rounded p-2 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
          placeholder="e.g., 5"
          required
        />
      </div>

      <div>
        <label className="block text-sm font-medium mb-1 text-gray-700">Pain (0-10)</label>
        <input 
          type="number" min="0" max="10"
          value={pain}
          onChange={(e) => setPain(e.target.value === '' ? '' : Number(e.target.value))}
          className="w-full border border-gray-300 rounded p-2 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
          placeholder="0 = No pain"
          required
        />
      </div>

      <div>
        <label className="block text-sm font-medium mb-1 text-gray-700">Notes</label>
        <textarea 
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className="w-full border border-gray-300 rounded p-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
          rows={3}
          placeholder="How did it feel? Any fatigue or soreness?"
        />
      </div>

      <div className="flex gap-2 pt-2">
        <button 
          type="submit"
          disabled={submitting}
          className="bg-blue-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-50 flex-1 transition-colors"
        >
          {submitting ? 'Saving...' : 'Save & Update Advice'}
        </button>
        
        {existingCheckIn && (
          <button 
            type="button"
            onClick={() => {
              // Reset values and exit edit mode
              setPain(existingCheckIn.pain_score);
              setEffort(existingCheckIn.rpe);
              setNotes(existingCheckIn.notes || '');
              setIsEditing(false);
            }}
            className="px-3 py-2 rounded text-sm font-medium text-gray-600 hover:bg-gray-200 border border-gray-300 transition-colors"
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
