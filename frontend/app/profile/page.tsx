'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import ConnectStravaButton from '@/components/ConnectStravaButton';
import LinkTelegramButton from '@/components/LinkTelegramButton';
import ImportStravaHistory from '@/components/ImportStravaHistory';
import ThemeToggle from '@/components/ThemeToggle';
import VoiceDialsPanel from '@/components/VoiceDialsPanel';
import StanceDialsPanel from '@/components/StanceDialsPanel';
import UserMaterialsPanel from '@/components/UserMaterialsPanel';
import FeatureDisabledGate from '@/components/FeatureDisabledGate';
import { useCoachFeatureFlags } from '@/lib/useCoachFeatureFlags';
import { fetchFromAPI } from '@/lib/api';

export default function ProfilePage() {
  const router = useRouter();
  const coachFlags = useCoachFeatureFlags();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  
  // Form State
  const [formData, setFormData] = useState({
    goal_type: 'general',
    experience_level: 'intermediate',
    weekly_days_available: 4,
    current_weekly_km: 0,
    injury_notes: '',
    // upcoming_races handling skipped for simple MVP form, 
    // but field exists in backend schema
    upcoming_races: [],
    max_hr: 0, // New field state
    resting_hr: 0,
    // #742: null, not 0. These post straight through to the coach pack, and the
    // backend rejects a physiologically impossible figure rather than coaching on
    // it — so the other fields' 0-means-empty sentinel would be a 422 here.
    weight_kg: null as number | null,
    height_cm: null as number | null,
    week_starts_on: 0 // 0=Monday (default), 6=Sunday (#676)
  });

  useEffect(() => {
    fetchFromAPI('/api/profile')
      .then(data => {
        if (!data) {
          setLoading(false);
          return;
        }
        setFormData({
            goal_type: data.goal_type || 'general',
            experience_level: data.experience_level || 'intermediate',
            weekly_days_available: data.weekly_days_available || 4,
            current_weekly_km: data.current_weekly_km || 0,
            injury_notes: data.injury_notes || '',
            upcoming_races: data.upcoming_races || [],
            max_hr: data.max_hr || 0,
            resting_hr: data.resting_hr || 0,
            weight_kg: data.weight_kg ?? null,
            height_cm: data.height_cm ?? null,
            week_starts_on: data.week_starts_on ?? 0
        });
        setLoading(false);
      })
      .catch(err => {
        console.error(err);
        setLoading(false);
      });
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      await fetchFromAPI('/api/profile', {
        method: 'PUT',
        body: JSON.stringify(formData),
      });
      router.push('/');
    } catch (err) {
      console.error(err);
      alert('Error updating profile');
      setSaving(false);
    }
  };

  // #742: clearing a body field must send null ("not stated"), never 0 — the coach
  // pack drops an unstated build rather than reading it as a real figure.
  const NULLABLE_NUMERIC = ['weight_kg', 'height_cm'];
  const NUMERIC = ['weekly_days_available', 'current_weekly_km', 'max_hr', 'resting_hr', 'week_starts_on'];

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({
        ...prev,
        [name]: NULLABLE_NUMERIC.includes(name)
          ? (value === '' ? null : Number(value))
          : NUMERIC.includes(name) ? Number(value) : value
    }));
  };

  if (loading) return <div className="p-8">Loading profile...</div>;

  return (
    <div className="max-w-2xl mx-auto p-4 md:p-8">
      <header className="mb-6 flex flex-wrap justify-between items-center gap-4">
        <h1 className="text-2xl font-bold whitespace-nowrap">Athlete Profile</h1>
        <div className="flex items-center gap-4 flex-wrap">
            <ThemeToggle />
            <ConnectStravaButton />
            <LinkTelegramButton />
            <Link href="/" className="text-blue-600 dark:text-blue-400 hover:underline">Cancel</Link>
        </div>
      </header>

      <div className="mb-6">
        <ImportStravaHistory />
      </div>

      <form onSubmit={handleSubmit} className="space-y-6 bg-white dark:bg-gray-800 p-6 border dark:border-gray-700 rounded-xl shadow-sm">
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
                <label className="block text-sm font-medium mb-1">Goal Type</label>
                <select 
                    name="goal_type" 
                    value={formData.goal_type} 
                    onChange={handleChange}
                    className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
                >
                    <option value="general">General Fitness</option>
                    <option value="5k">5k</option>
                    <option value="10k">10k</option>
                    <option value="half">Half Marathon</option>
                    <option value="marathon">Marathon</option>
                </select>
            </div>

            <div>
                <label className="block text-sm font-medium mb-1">Experience Level</label>
                <select 
                    name="experience_level" 
                    value={formData.experience_level} 
                    onChange={handleChange}
                    className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
                >
                    <option value="new">Beginner</option>
                    <option value="intermediate">Intermediate</option>
                    <option value="advanced">Advanced</option>
                </select>
            </div>

            <div>
                <label className="block text-sm font-medium mb-1">Weekly Days Available</label>
                <input 
                    type="number" min="1" max="7"
                    name="weekly_days_available"
                    value={formData.weekly_days_available}
                    onChange={handleChange}
                    className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
                />
            </div>

            <div>
                <label className="block text-sm font-medium mb-1">Max Heart Rate (bpm)</label>
                <input 
                    type="number" min="100" max="250"
                    name="max_hr"
                    value={formData.max_hr || ''}
                    onChange={handleChange}
                    placeholder="e.g. 190"
                    className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
                />
                 <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    Used to calculate zones. If unknown, estimate with 220 minus age.
                </p>
            </div>

            <div>
                <label className="block text-sm font-medium mb-1">Resting Heart Rate (bpm)</label>
                <input
                    type="number" min="30" max="120"
                    name="resting_hr"
                    value={formData.resting_hr || ''}
                    onChange={handleChange}
                    placeholder="e.g. 50"
                    className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
                />
                 <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    Your typical morning resting HR. Helps the coach read fatigue trends.
                </p>
            </div>

            <div>
                <label className="block text-sm font-medium mb-1">Weight (kg)</label>
                <input
                    type="number" min="20" max="300" step="0.1"
                    name="weight_kg"
                    value={formData.weight_kg ?? ''}
                    onChange={handleChange}
                    placeholder="e.g. 72.5"
                    className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
                />
            </div>

            <div>
                <label className="block text-sm font-medium mb-1">Height (cm)</label>
                <input
                    type="number" min="100" max="250" step="0.5"
                    name="height_cm"
                    value={formData.height_cm ?? ''}
                    onChange={handleChange}
                    placeholder="e.g. 178"
                    className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
                />
                 <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    Optional. Your coach uses your build to judge how fast to ramp volume
                    and how much strength work to prescribe &mdash; not to set a target
                    weight. Leave blank and it simply won&apos;t be considered.
                </p>
            </div>

            <div>
                <label className="block text-sm font-medium mb-1">Current Weekly Volume (km)</label>
                <input
                    type="number" min="0"
                    name="current_weekly_km"
                    value={formData.current_weekly_km}
                    onChange={handleChange}
                    className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
                />
            </div>

            <div>
                <label className="block text-sm font-medium mb-1">Week Starts On</label>
                <select
                    name="week_starts_on"
                    value={formData.week_starts_on}
                    onChange={handleChange}
                    className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
                >
                    <option value={0}>Monday</option>
                    <option value={6}>Sunday</option>
                </select>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    Sets which day your training week begins, for &ldquo;this week&rdquo; on the coach and Trends.
                </p>
            </div>
        </div>

        <div>
            <label className="block text-sm font-medium mb-1">Injury / Health Notes</label>
            <textarea 
                name="injury_notes"
                value={formData.injury_notes}
                onChange={handleChange}
                rows={3}
                placeholder="Any nagging pains or past injuries?"
                className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
            />
        </div>

        <button 
            type="submit" 
            disabled={saving}
            className="w-full bg-blue-600 text-white py-2 px-4 rounded hover:bg-blue-700 disabled:opacity-50"
        >
            {saving ? 'Saving...' : 'Save Profile'}
        </button>

      </form>

      <div className="mt-6">
        <FeatureDisabledGate
          disabled={!coachFlags.voice}
          note="Voice is turned off in the coach configuration, so these settings have no effect right now."
        >
          <VoiceDialsPanel />
        </FeatureDisabledGate>
      </div>

      <div className="mt-6">
        <FeatureDisabledGate
          disabled={!coachFlags.stance}
          note="Stance is turned off in the coach configuration, so these settings have no effect right now."
        >
          <StanceDialsPanel />
        </FeatureDisabledGate>
      </div>

      <div className="mt-6">
        <FeatureDisabledGate
          disabled={!coachFlags.user_materials}
          note="Coaching materials are turned off in the coach configuration, so uploads have no effect right now."
        >
          <UserMaterialsPanel />
        </FeatureDisabledGate>
      </div>
    </div>
  );
}
