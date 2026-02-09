'use client';
import { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';

const OPTIONS_BY_TYPE: Record<string, string[]> = {
  Run: ["Easy Run", "Recovery", "Long Run", "Tempo", "Intervals", "Hills", "Race", "Treadmill"],
  Walk: ["Leisure Walk", "Power Walk", "Hike", "Commute"],
  Ride: ["Easy Ride", "Workout", "Long Ride", "Race", "Indoor Ride"],
  Swim: ["Endurance", "Drills", "Intervals", "Race"],
  Workout: ["Strength", "Cardio", "Yoga", "Mobility"],
  Default: ["Easy", "Moderate", "Hard", "Workout"]
};

interface CheckInData {
  rpe: number;
  pain_score: number;
  notes?: string | null;
}

interface Props {
  activityId: string;
  existingCheckIn?: CheckInData | null;
  currentType?: string | null;
  assignedClass?: string;
  sportType?: string; // e.g. "Run", "Walk"
}

export default function CheckInForm({ activityId, existingCheckIn, currentType, assignedClass, sportType = "Run" }: Props) {
  const router = useRouter();
  
  // Determine available options based on sport type
  const typeOptions = useMemo(() => {
     // Normalize key (Strava uses "Run", "Walk", "Ride" etc)
     const key = Object.keys(OPTIONS_BY_TYPE).find(k => k === sportType) || 'Default';
     return OPTIONS_BY_TYPE[key];
  }, [sportType]);

  // Mode state: If data exists, start in View Mode (isEditing = false)
  const [isEditing, setIsEditing] = useState(!existingCheckIn);
  
  // Form State
  const [pain, setPain] = useState<number | ''>(existingCheckIn?.pain_score ?? '');
  const [effort, setEffort] = useState<number | ''>(existingCheckIn?.rpe ?? '');
  const [notes, setNotes] = useState(existingCheckIn?.notes ?? '');
  
  // Intent State
  const [intent, setIntent] = useState(currentType || assignedClass || "Easy Run");

  const [submitting, setSubmitting] = useState(false);

  // Sync state with props when server data updates
  useEffect(() => {
    if (existingCheckIn) {
      setPain(existingCheckIn.pain_score);
      setEffort(existingCheckIn.rpe);
      setNotes(existingCheckIn.notes || '');
      if (!isEditing) setIsEditing(false);
    }
    // Also sync intent if it changes externally or on load
    setIntent(currentType || assignedClass || typeOptions[0]);
  }, [existingCheckIn, currentType, assignedClass]); 
  
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    
    try {
        const checkInPayload = {
            rpe: effort === '' ? null : Number(effort),
            pain_score: pain === '' ? null : Number(pain),
            notes: notes || null
        };
        
        // Parallel requests: Save Check-In AND Save Intent
        const promises = [
            fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'}/api/activities/${activityId}/checkin`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(checkInPayload)
            })
        ];

        // Only update intent if logic dictates (or always to be safe/simple)
        // The endpoint is PUT /intent
        promises.push(
            fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'}/api/activities/${activityId}/intent`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_intent: intent })
            })
        );
        
        const results = await Promise.all(promises);
        results.forEach(res => {
            if (!res.ok) throw new Error("Failed to save data");
        });
        
        // Optimistic update / switch to view
        setIsEditing(false);
        router.refresh(); 
    } catch (err) {
        console.error(err);
        alert("Error saving check-in data");
    } finally {
        setSubmitting(false);
    }
  }

  // --- VIEW MODE ---
  if (!isEditing && existingCheckIn) {
    return (
      <div className="bg-green-50 rounded-lg shadow-sm border border-green-200 p-4">
          <div className="flex justify-between items-start mb-3">
              <h3 className="font-semibold text-green-800">Activity Report</h3>
              <button 
                onClick={() => setIsEditing(true)}
                className="text-xs font-medium text-green-700 hover:text-green-900 underline"
              >
                Edit
              </button>
          </div>
          <dl className="grid grid-cols-2 gap-4 text-sm text-green-700">
              <div className="col-span-2 flex justify-between border-b border-green-100 pb-1">
                  <dt className="text-green-800 opacity-80">Type</dt>
                  <dd className="font-medium text-right">
                       {intent}
                       <span className="text-[10px] block opacity-60 font-normal">
                         {currentType ? '(Manual)' : '(Auto)'}
                       </span>
                  </dd>
              </div>
              <div className="flex justify-between border-b border-green-100 pb-1">
                  <dt className="text-green-800 opacity-80">RPE</dt>
                  <dd className="font-medium">{existingCheckIn.rpe}/10</dd>
              </div>
              <div className="flex justify-between border-b border-green-100 pb-1">
                  <dt className="text-green-800 opacity-80">Pain</dt>
                  <dd className="font-medium">{existingCheckIn.pain_score}/10</dd>
              </div>
              {existingCheckIn.notes && (
                  <div className="col-span-2 pt-1">
                       <dt className="text-xs font-bold uppercase tracking-wider text-green-800 opacity-60 mb-1">Notes</dt>
                       <dd className="italic bg-white/50 p-2 rounded border border-green-100/50 text-green-900 break-words">
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
    <form onSubmit={handleSubmit} className="space-y-4 bg-gray-50 p-4 rounded-lg border border-gray-100 shadow-sm">
      <div className="flex justify-between items-center">
        <h3 className="font-semibold text-gray-800">
            {existingCheckIn ? 'Edit Report' : 'Activity Check-In'}
        </h3>
      </div>
      
      {/* Intent Selection */}
      <div>
        <label className="block text-sm font-medium mb-1 text-gray-700">Activity Type</label>
        <select
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          className="w-full border border-gray-300 rounded p-2 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all bg-white"
        >
          {typeOptions.map(type => (
              <option key={type} value={type}>{type}</option>
          ))}
        </select>
        {assignedClass && typeOptions.includes(assignedClass) && (
            <p className="text-xs text-gray-400 mt-1">
                Detected as: {assignedClass}
            </p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
            <label className="block text-sm font-medium mb-1 text-gray-700">RPE (1-10)</label>
            <input 
            type="number" min="1" max="10"
            value={effort}
            onChange={(e) => setEffort(e.target.value === '' ? '' : Number(e.target.value))}
            className="w-full border border-gray-300 rounded p-2 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
            placeholder="Difficuty"
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
            placeholder="Soreness"
            required
            />
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium mb-1 text-gray-700">Notes</label>
        <textarea 
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className="w-full border border-gray-300 rounded p-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
          rows={3}
          placeholder="How did it feel? Any fatigue, aches, or specific context?"
        />
      </div>

      <div className="flex gap-2 pt-2 border-t border-gray-200 mt-2">
        {existingCheckIn && (
          <button 
            type="button"
            onClick={() => {
              // Reset values and exit edit mode
              setPain(existingCheckIn.pain_score);
              setEffort(existingCheckIn.rpe);
              setNotes(existingCheckIn.notes || '');
              setIntent(currentType || assignedClass || typeOptions[0]);
              setIsEditing(false);
            }}
            className="px-3 py-2 rounded text-sm font-medium text-gray-600 hover:bg-gray-200 border border-gray-300 transition-colors"
          >
            Cancel
          </button>
        )}

        <button 
          type="submit"
          disabled={submitting}
          className="bg-blue-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-50 flex-1 transition-colors"
        >
          {submitting ? 'Saving...' : 'Save Report'}
        </button>
      </div>
    </form>
  );
}
