import type { Metadata } from 'next';
import LegalPage from '@/components/legal/LegalPage';

// #964: public privacy policy. Required before the Google OAuth app can leave
// Testing mode (which blocks the Clerk production cutover, #626), and needed on
// its own terms — this app processes heart-rate series, injury and pain notes,
// stated body measurements and free-text health commentary, and forwards
// several of those to a third-party model provider.
//
// The content describes the system AS BUILT. If what the app collects or who it
// is sent to changes, this page changes in the same PR.

export const metadata: Metadata = {
  title: 'Privacy Policy · PulseCoach AI',
  description:
    'What PulseCoach AI collects, why, who it is shared with, and how to delete it.',
};

const CONTACT = 'philippe@twohourssleep.com';

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy Policy" updated="24 August 2026">
      <p>
        PulseCoach AI (&ldquo;the app&rdquo;) is a running coaching service that connects to your
        Strava account, analyses your training, and writes coaching feedback. This
        page explains what it collects, why, who else sees it, and how to get rid
        of it.
      </p>
      <p>
        It is written to be read, not to be survived. If anything here is unclear
        or looks wrong, email{' '}
        <a href={`mailto:${CONTACT}`}>{CONTACT}</a> and ask.
      </p>

      <h2>Health data, stated plainly</h2>
      <p>
        Much of what this app handles is health information about you: heart-rate
        recordings, perceived exertion, pain reports, sleep quality, injuries, and
        any weight or height you choose to state. Some of it is sent to a
        third-party AI provider so the coach can write about it (see{' '}
        <a href="#sharing">Who else sees your data</a>).
      </p>
      <p>
        If you are not comfortable with that, do not connect the app. You can also
        use it while leaving the optional fields blank; the coach works with less
        and will say so rather than guess.
      </p>

      <h2>What the app collects</h2>

      <h3>Your account</h3>
      <p>
        You sign in with a social login handled by <strong>Clerk</strong>. The app
        receives your verified email address, and whatever name and profile image
        that provider passes along. The verified email is your identity in the
        app: sign in again with the same address and you land back on your own
        data. The app never receives or stores your social account password.
      </p>

      <h3>From Strava, once you connect it</h3>
      <ul>
        <li>Your Strava athlete ID and access tokens for your account.</li>
        <li>
          Activity records: type, start time, distance, duration, elevation,
          average and maximum heart rate, recorded laps, and the rest of the
          summary Strava provides.
        </li>
        <li>
          Per-sample time-series streams for those activities: heart rate, pace,
          cadence and power.
        </li>
        <li>Your heart-rate zone boundaries as configured in Strava.</li>
      </ul>
      <p>
        The app only ever reads from Strava. It does not post, edit or delete
        anything in your Strava account.
      </p>

      <h3>What you tell the app directly</h3>
      <ul>
        <li>
          Profile: your goal, experience level, typical weekly volume, maximum
          heart rate, upcoming races, injuries, and optionally your weight and
          height.
        </li>
        <li>
          Post-run check-ins: perceived effort, pain, sleep quality, and free-text
          notes.
        </li>
        <li>Your conversations with the coach, and how you want it to sound.</li>
        <li>Any coaching material you upload for the coach to read.</li>
        <li>
          Your Telegram chat ID, only if you choose to link Telegram for
          notifications.
        </li>
      </ul>

      <h3>What the app derives</h3>
      <p>
        From the above it computes training metrics, training load and fitness
        estimates, rolling baselines, a written memory of what you have told it,
        and the coaching reports themselves. These are stored alongside your
        account.
      </p>

      <h2>Why the app uses it</h2>
      <ul>
        <li>To analyse your runs and produce coaching feedback.</li>
        <li>
          To compare a session against your own history rather than a population
          average.
        </li>
        <li>To send you notifications you have asked for.</li>
        <li>To keep the service running, and to diagnose faults.</li>
      </ul>
      <p>
        Your data is not sold. It is not used for advertising. It is not used to
        train anyone&rsquo;s AI models.
      </p>

      <h2>Legal basis</h2>
      <p>
        Where UK or EU data protection law applies, the app relies on your{' '}
        <strong>explicit consent</strong> to process health data, given when you
        connect Strava and when you fill in optional health fields. Account and
        service operation rely on performance of a contract with you. You can
        withdraw consent at any time by disconnecting Strava or deleting your
        account, which is described below.
      </p>

      <h2 id="sharing">Who else sees your data</h2>
      <p>
        The app is run by one person and uses a small number of service providers.
        Each one only receives what it needs.
      </p>
      <ul>
        <li>
          <strong>Clerk</strong> — sign-in and session management. Holds your
          email and social profile.
        </li>
        <li>
          <strong>Strava</strong> — the source of your activity data, under the
          permissions you granted.
        </li>
        <li>
          <strong>Anthropic</strong> — the AI provider that writes your coaching
          reports and chat replies. It receives the training context assembled for
          a given run or conversation, which can include your metrics, your
          check-in notes, your stated profile, your messages to the coach, and any
          material you uploaded. It does not receive your email address or your
          Strava tokens.
        </li>
        <li>
          <strong>Telegram</strong> — only if you link it, and only to deliver
          your notifications.
        </li>
        <li>
          <strong>Railway</strong> and <strong>Vercel</strong> — hosting for the
          application, database and website.
        </li>
        <li>
          <strong>Sentry</strong> — error diagnostics, only when enabled. Receives
          technical error details, not your training data.
        </li>
      </ul>
      <p>
        Beyond these, your data is disclosed only if the law requires it.
      </p>

      <h2>Where it is stored</h2>
      <p>
        Your data lives in a managed PostgreSQL database and Redis instance hosted
        by Railway, with the website served by Vercel. The providers listed above
        operate internationally, so your data may be processed outside the country
        you live in, including in the United States.
      </p>

      <h2>How long it is kept</h2>
      <p>
        Your data is kept for as long as your account exists. There is no
        automatic expiry, because the coach&rsquo;s value comes from your history:
        it compares this week against your own last two years, not against a
        typical runner.
      </p>
      <p>When you delete your account, it goes.</p>

      <h2>Deleting your data</h2>
      <p>You have two levers, and both work immediately.</p>
      <ul>
        <li>
          <strong>Disconnect Strava</strong> — stops any further activity data
          reaching the app.
        </li>
        <li>
          <strong>Delete your account</strong>, from your profile page. This
          removes your sign-in record first, so the account cannot be reopened into
          an empty shell, and then deletes every row the app holds about you in a
          single operation: activities, streams, derived metrics, coaching reports,
          conversations, check-ins, memory, uploaded materials, baselines, your
          Strava connection and your profile.
        </li>
      </ul>
      <p>
        This is a real deletion, not a flag. It cannot be undone, and the app
        cannot recover your history afterwards. Backups held by the hosting
        providers may retain copies for a short period before rotating out.
      </p>

      <h2>Your rights</h2>
      <p>
        If you are in the UK or the EU, you have the right to access your data,
        correct it, delete it, receive a portable copy, restrict or object to its
        processing, and withdraw consent. Deletion is built into the app as
        described above. For anything else, email{' '}
        <a href={`mailto:${CONTACT}`}>{CONTACT}</a> and it will be handled within
        one month.
      </p>
      <p>
        You also have the right to complain to your data protection authority. In
        the UK that is the Information Commissioner&rsquo;s Office.
      </p>

      <h2>Cookies and local storage</h2>
      <p>
        The app sets a session cookie through Clerk so you stay signed in. It
        stores your light/dark theme preference in your browser. There are no
        advertising cookies and no third-party analytics trackers.
      </p>

      <h2>Children</h2>
      <p>
        The app is not intended for anyone under 16, and accounts should not be
        created for them.
      </p>

      <h2>Security</h2>
      <p>
        Traffic is encrypted in transit. Access to the production database is
        restricted to the app and its operator. No system is perfectly secure, and
        this one is operated by an individual rather than a company with a security
        team, which is worth knowing when you decide what to put in a free-text
        note.
      </p>

      <h2>Changes to this policy</h2>
      <p>
        If what the app collects or who it shares with changes, this page changes
        with it and the date at the top moves. Material changes will be flagged in
        the app.
      </p>

      <h2>Contact</h2>
      <p>
        Questions, requests, or corrections:{' '}
        <a href={`mailto:${CONTACT}`}>{CONTACT}</a>.
      </p>
    </LegalPage>
  );
}
