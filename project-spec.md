# Project Spec

Version: 1.0.0
This file documents the current implementation truth for the Running Coach (Strava) repository.

## Product Summary
- The product is a local-first web app that connects to Strava to ingest running activities, compute training signals, and display actionable analysis.
- The primary users are runners seeking short, actionable training analysis without relying on perfect physiological data.
- The core user flow involves connecting a Strava account to trigger activity syncs and subsequently reviewing computed run metrics and weekly training dashboards.

## Domain Concepts
- Activity represents a single running workout ingested from Strava, including summary statistics and optional streams.
- DerivedMetric holds the computed analysis for an activity, including classification, effort score, and generated flags.
- UserProfile stores user goals, experience level, availability, and injury notes which tune the analysis.
- CheckIn captures manual user feedback like RPE, pain, and sleep quality linked to a specific activity.
- The system associates exactly one DerivedMetric and optional CheckIn with each ingested Activity.

## Scope
- The product currently supports fetching the last 30 days of activities through OAuth and receiving updates via webhooks.
- Major user-visible workflows include the Strava OAuth connection, viewing the weekly trends dashboard, and inspecting individual activity metrics and flags.
- The analysis explicitly excludes diagnosing medical injuries or giving professional medical advice.

## Important Constraints
- The system must fall back to conservative analysis when data confidence is low.
- Synchronization prefers background webhook-driven updates over active polling to avoid restrictive Strava rate limits.
- The app fundamentally requires a local Docker setup with PostgreSQL and Redis containers for state preservation and task queueing.

## Architecture Summary
- A decoupled architecture leveraging a RESTful Python API backend and a server-side rendered React frontend.
- The main runtime layers are a FastAPI application handling API logic and RQ workers, communicating with a Next.js UI using the App Router.
- The primary data flow starts with webhook events triggering background RQ jobs that sync Strava data into PostgreSQL, which are then processed synchronously to produce insights.
- The system primarily communicates with the external Strava API boundary for remote data ingestion and OAuth credential management.

## Key Dependencies
- FastAPI: Provides the backend REST API framework for endpoints and request routing.
- SQLAlchemy: Manages the PostgreSQL database definitions and object-relational mapping.
- RQ and Redis: Provide the worker queue framework for processing background data synchronizations.
- Next.js: Provides the App Router framework serving the dashboard, routing views, and client components.

## Project Structure
- `backend/app/api/`: Owns the FastAPI route definitions.
- `backend/app/models/`: Owns the SQLAlchemy ORM models denoting database tables.
- `backend/app/services/`: Owns the core domain logic, analysis classifications, and the Strava integration.
- `backend/app/jobs/`: Owns the background RQ task pipeline like the `strava_sync` logic.
- `frontend/app/`: Owns the Next.js pages corresponding to application paths.
- `frontend/components/`: Owns reusable React components and visualisations.

## Testing Overview
- The backend utilizes the pytest framework for unit testing, integration logic, and validation bounds.
- Current automated coverage asserts accuracy within models, core analytical policies, interval extraction rules, and workout matching pipelines.
- A significant testing gap exists in frontend component interactions and comprehensive end-to-end user browser verification flows.

## Maintenance Checklist
- Update this file when routes, schema, sync rules, or provider behavior changes.
- Keep this file aligned with the current codebase, not planned architecture.
- Keep this file concise and factual.
