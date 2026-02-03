'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

export default function ProfilePage() {
  const router = useRouter();
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
    max_hr: 0 // New field state
  });

  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'}/api/profile`)
      .then(res => {
        if (!res.ok) throw new Error('Failed to load profile');
        return res.json();
      })
      .then(data => {
        setFormData({
            goal_type: data.goal_type || 'general',
            experience_level: data.experience_level || 'intermediate',
            weekly_days_available: data.weekly_days_available || 4,
            current_weekly_km: data.current_weekly_km || 0,
            injury_notes: data.injury_notes || '',
            upcoming_races: data.upcoming_races || [],
            max_hr: data.max_hr || 0
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
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'}/api/profile`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      });
      if (!res.ok) throw new Error('Failed to update');
      router.push('/');
    } catch (err) {
      console.error(err);
      alert('Error updating profile');
      setSaving(false);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({
        ...prev,
        [name]: name === 'weekly_days_available' || name === 'current_weekly_km' || name === 'max_hr' ? Number(value) : value
    }));
  };

  if (loading) return <div className="p-8">Loading profile...</div>;

  return (
    <div className="max-w-2xl mx-auto p-4 md:p-8">
      <header className="mb-6 flex justify-between items-center">
        <h1 className="text-2xl font-bold">Athlete Profile</h1>
        <Link href="/" className="text-blue-600 hover:underline">Cancel</Link>
      </header>
      
      <form onSubmit={handleSubmit} className="space-y-6 bg-white p-6 border rounded-xl shadow-sm">
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
                <label className="block text-sm font-medium mb-1">Goal Type</label>
                <select 
                    name="goal_type" 
                    value={formData.goal_type} 
                    onChange={handleChange}
                    className="w-full border rounded p-2"
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
                    className="w-full border rounded p-2"
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
                    className="w-full border rounded p-2"
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
                    className="w-full border rounded p-2"
                />
                 <p className="text-xs text-gray-500 mt-1">
                    Used to calculate zones. If unknown, estimate with 220 minus age.
                </p>
            </div>

            <div>
                <label className="block text-sm font-medium mb-1">Current Weekly Volume (km)</label>
                <input 
                    type="number" min="0"
                    name="current_weekly_km"
                    value={formData.current_weekly_km}
                    onChange={handleChange}
                    className="w-full border rounded p-2"
                />
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
                className="w-full border rounded p-2"
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
    </div>
  );
}
