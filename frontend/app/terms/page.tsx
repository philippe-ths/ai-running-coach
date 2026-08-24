import type { Metadata } from 'next';
import LegalPage from '@/components/legal/LegalPage';

// #964: public terms of service. Required before the Google OAuth app can leave
// Testing mode (which blocks the Clerk production cutover, #626).
//
// The "not medical advice" section is not boilerplate: it is the user-facing
// statement of the same boundary the backend enforces mechanically in
// services/coach/validator.py, whose medical-scope rule refuses dose advice,
// diagnosis verbs, medication directives and asserted clinical conditions in
// every coach output. If that boundary ever moves, this section moves with it.

export const metadata: Metadata = {
  title: 'Terms of Service · PulseCoach AI',
  description:
    'The terms you agree to when using PulseCoach AI, including what the coaching is and is not.',
};

const CONTACT = 'philippe@twohourssleep.com';

export default function TermsPage() {
  return (
    <LegalPage title="Terms of Service" updated="24 August 2026">
      <p>
        These terms cover your use of PulseCoach AI (&ldquo;the app&rdquo;). By
        creating an account you agree to them. If you do not, do not use the app.
      </p>

      <h2>What the app is</h2>
      <p>
        The app connects to your Strava account, analyses your running, and
        produces written coaching feedback using an AI model. It is a personal
        project run by an individual, not a company, and it is offered free of
        charge.
      </p>

      <h2>This is not medical advice</h2>
      <p>
        This is the most important section on the page.
      </p>
      <p>
        The app is a training tool. It is <strong>not</strong> a medical device,
        and the coaching it produces is <strong>not</strong> medical advice,
        diagnosis or treatment. The coach is built to stay inside that boundary:
        it will not prescribe medication or dosages, will not diagnose you, and
        will not assert that you have a clinical condition. When your data shows a
        pattern worth a professional&rsquo;s eyes, it will say so and suggest you
        speak to one.
      </p>
      <p>
        It can still be wrong. It works from imperfect sensor data and from what
        you tell it, and an AI model can misread either. Use your judgement.
      </p>
      <p>
        <strong>
          If you are injured, in pain, unwell, or considering a significant change
          in training, speak to a doctor or a qualified professional. If you think
          you are having a medical emergency, contact your local emergency
          services immediately.
        </strong>{' '}
        Do not delay seeking medical advice because of something the app said, and
        do not use it as a substitute for care.
      </p>
      <p>
        You train at your own risk. You are responsible for deciding what to
        actually do.
      </p>

      <h2>Your account</h2>
      <ul>
        <li>You must be at least 16 years old.</li>
        <li>
          Sign in through the social login provided. Keep that account secure;
          anyone with access to it has access to your training data.
        </li>
        <li>The account is yours alone. Do not share it.</li>
        <li>Provide accurate information. The coaching is only as good as it.</li>
      </ul>

      <h2>Connecting Strava</h2>
      <p>
        Connecting Strava is optional but the app does very little without it. You
        grant read access to your activity data, which you can revoke at any time
        from your Strava settings or by disconnecting inside the app. The app never
        writes to your Strava account. Your use of Strava remains governed by
        Strava&rsquo;s own terms.
      </p>

      <h2>Acceptable use</h2>
      <p>Do not:</p>
      <ul>
        <li>Use the app for anyone other than yourself, or upload someone else&rsquo;s data without their consent.</li>
        <li>Attempt to access another user&rsquo;s account or data.</li>
        <li>Probe, scrape, overload or reverse-engineer the service.</li>
        <li>Use it for anything unlawful.</li>
        <li>
          Try to steer the coach into producing medical, diagnostic or otherwise
          harmful output.
        </li>
      </ul>

      <h2>What you upload</h2>
      <p>
        You keep ownership of everything you put into the app: your notes, your
        messages, and any coaching material you upload. You grant permission to
        store and process it for the purpose of running the service and generating
        your coaching, as set out in the{' '}
        <a href="/privacy">Privacy Policy</a>.
      </p>
      <p>
        Do not upload material you do not have the right to share. Uploaded
        material informs how the coach reasons; it does not override your safety or
        the boundaries described above.
      </p>

      <h2>Availability</h2>
      <p>
        The app is provided as-is and as-available, with no warranty of any kind.
        It is actively developed and may change, break, or go down without notice.
        Features may be added or removed. There is no uptime commitment and no
        support obligation.
      </p>
      <p>
        Keep your own records if your training history matters to you. Strava
        remains the source of truth for your activities.
      </p>

      <h2>Limitation of liability</h2>
      <p>
        To the fullest extent the law allows, the operator is not liable for any
        injury, loss, damage, lost data or lost opportunity arising from your use
        of the app or from decisions you made based on its output.
      </p>
      <p>
        Nothing in these terms limits liability for death or personal injury caused
        by negligence, for fraud, or for anything else that cannot lawfully be
        excluded. If you are a consumer, your statutory rights are unaffected.
      </p>

      <h2>Ending your use</h2>
      <p>
        You can delete your account at any time from your profile page, which
        permanently removes your data as described in the{' '}
        <a href="/privacy">Privacy Policy</a>. Access may be suspended or removed
        if these terms are breached, or if the service is discontinued. Reasonable
        notice will be given before shutting the service down, where possible.
      </p>

      <h2>Changes to these terms</h2>
      <p>
        These terms may change. The date at the top moves when they do, and
        material changes will be flagged in the app. Continuing to use the app
        after a change means you accept it.
      </p>

      <h2>Contact</h2>
      <p>
        Questions about these terms:{' '}
        <a href={`mailto:${CONTACT}`}>{CONTACT}</a>.
      </p>
    </LegalPage>
  );
}
