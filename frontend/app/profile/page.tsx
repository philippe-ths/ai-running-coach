'use client';

import { Suspense, useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { AlertTriangle, CheckCircle2 } from 'lucide-react';
import ConnectStravaButton from '@/components/ConnectStravaButton';
import LinkTelegramButton from '@/components/LinkTelegramButton';
import ImportStravaHistory from '@/components/ImportStravaHistory';
import ThemeToggle from '@/components/ThemeToggle';
import VoiceDialsPanel from '@/components/VoiceDialsPanel';
import StanceDialsPanel from '@/components/StanceDialsPanel';
import UserMaterialsPanel from '@/components/UserMaterialsPanel';
import FeatureDisabledGate from '@/components/FeatureDisabledGate';
import ProfileHub from '@/components/profile/ProfileHub';
import SectionScreen from '@/components/profile/SectionScreen';
import {
  AppSection,
  BodySection,
  HealthSection,
  TrainingSection,
} from '@/components/profile/EditSections';
import {
  coerceField,
  EMPTY_PROFILE_FORM,
  ProfileForm,
  profileFromApi,
} from '@/components/profile/profileForm';
import { useCoachFeatureFlags } from '@/lib/useCoachFeatureFlags';
import { fetchFromAPI } from '@/lib/api';

// #941: the profile is a hub of current values with focused screens behind each
// row, not one long form. Which screen is showing is a `?s=` query param on this
// same route, so the component never unmounts -- no refetch when you open a
// screen, and hardware Back returns you to the hub instead of leaving the page.

export default function ProfilePage() {
  // The bottom padding clears the coach launcher. The layout's `main` reserves
  // room for the tab bar only, and the launcher floats ~2.5rem above that, so
  // without this it lands on top of the last row -- which on the hub is a link,
  // so it was covering a tap target, not just some text.
  //
  // useSearchParams needs a Suspense boundary: /profile is statically
  // prerendered, and without one the whole route deopts to client rendering.
  return (
    <div className="pb-14 md:pb-0">
      <Suspense fallback={<div className="p-8">Loading profile...</div>}>
        <ProfileScreens />
      </Suspense>
    </div>
  );
}

function ProfileScreens() {
  const router = useRouter();
  const section = useSearchParams().get('s');
  const coachFlags = useCoachFeatureFlags();

  // `profile` is what is stored; `draft` is what the open screen is editing.
  const [profile, setProfile] = useState<ProfileForm>(EMPTY_PROFILE_FORM);
  const [draft, setDraft] = useState<ProfileForm>(EMPTY_PROFILE_FORM);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [justSaved, setJustSaved] = useState(false);

  useEffect(() => {
    fetchFromAPI('/api/profile')
      .then((data) => {
        const loaded = profileFromApi(data);
        setProfile(loaded);
        setDraft(loaded);
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setLoading(false);
      });
  }, []);

  // Opening or leaving a screen starts from what is stored, so edits abandoned
  // with Back are discarded rather than silently carried into the next save.
  useEffect(() => {
    setDraft(profile);
    setError(null);
  }, [section, profile]);

  const set = useCallback((name: keyof ProfileForm, value: string) => {
    setDraft((prev) => ({ ...prev, [name]: coerceField(name, value) }));
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      // The whole object, exactly as the flat form posted it: PUT /api/profile
      // validates against UserProfileCreate, which requires goal_type,
      // experience_level and weekly_days_available on every request.
      const updated = await fetchFromAPI('/api/profile', {
        method: 'PUT',
        body: JSON.stringify(draft),
      });
      setProfile(updated ? profileFromApi(updated) : draft);
      setJustSaved(true);
      router.replace('/profile', { scroll: false });
    } catch (err) {
      console.error(err);
      // Stay on the screen with the edits intact. The flat form raised a
      // browser alert() and left the runner to work out what had happened.
      setError('Could not save your profile. Check your connection and try again.');
    } finally {
      setSaving(false);
    }
  }, [draft, router]);

  useEffect(() => {
    if (!justSaved) return;
    const timer = setTimeout(() => setJustSaved(false), 4000);
    return () => clearTimeout(timer);
  }, [justSaved]);

  if (loading) return <div className="p-8">Loading profile...</div>;

  const saveProps = { onSave: handleSave, saving };

  const errorBanner = error ? (
    <div
      role="alert"
      className="mb-4 flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-900/30 dark:text-red-200"
    >
      <AlertTriangle size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
      <span>{error}</span>
    </div>
  ) : null;

  switch (section) {
    case 'training':
      return (
        <SectionScreen
          title="Training goal"
          description="Your goal shapes every session the coach prescribes."
          {...saveProps}
        >
          {errorBanner}
          <TrainingSection form={draft} onChange={set} />
        </SectionScreen>
      );

    case 'body':
      return (
        <SectionScreen
          title="Your body"
          description="What the coach uses to set your zones and judge how fast to ramp."
          {...saveProps}
        >
          {errorBanner}
          <BodySection form={draft} onChange={set} />
        </SectionScreen>
      );

    case 'health':
      return (
        <SectionScreen title="Injuries & health" {...saveProps}>
          {errorBanner}
          <HealthSection form={draft} onChange={set} />
        </SectionScreen>
      );

    case 'app':
      return (
        <SectionScreen title="App preferences" {...saveProps}>
          {errorBanner}
          <AppSection form={draft} onChange={set} themeControl={<ThemeToggle />} />
        </SectionScreen>
      );

    // The screens below own their own saving, so they get no Save in the
    // header -- which is the point of splitting them out.
    case 'connections':
      return (
        <SectionScreen
          title="Connections"
          description="Where your runs come from, and where your coach reaches you."
        >
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-4 rounded-xl border bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-800">
              <ConnectStravaButton />
            </div>
            <div className="flex flex-wrap items-center gap-4 rounded-xl border bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-800">
              <LinkTelegramButton />
            </div>
            <ImportStravaHistory />
          </div>
        </SectionScreen>
      );

    case 'voice':
      return (
        <SectionScreen title="Coach voice">
          <FeatureDisabledGate
            disabled={!coachFlags.voice}
            note="Voice is turned off in the coach configuration, so these settings have no effect right now."
          >
            <VoiceDialsPanel />
          </FeatureDisabledGate>
        </SectionScreen>
      );

    case 'stance':
      return (
        <SectionScreen title="Coach stance">
          <FeatureDisabledGate
            disabled={!coachFlags.stance}
            note="Stance is turned off in the coach configuration, so these settings have no effect right now."
          >
            <StanceDialsPanel />
          </FeatureDisabledGate>
        </SectionScreen>
      );

    case 'materials':
      return (
        <SectionScreen title="Coaching materials">
          <FeatureDisabledGate
            disabled={!coachFlags.user_materials}
            note="Coaching materials are turned off in the coach configuration, so uploads have no effect right now."
          >
            <UserMaterialsPanel />
          </FeatureDisabledGate>
        </SectionScreen>
      );

    // No section, or one this build does not know: the hub.
    default:
      return (
        <>
          {justSaved && (
            <div
              role="status"
              className="mx-auto mb-4 flex max-w-2xl items-center gap-2 rounded-lg border border-green-300 bg-green-50 p-3 text-sm text-green-800 dark:border-green-800 dark:bg-green-900/30 dark:text-green-200"
            >
              <CheckCircle2 size={16} aria-hidden="true" className="shrink-0" />
              Profile saved.
            </div>
          )}
          <ProfileHub form={profile} />
        </>
      );
  }
}
