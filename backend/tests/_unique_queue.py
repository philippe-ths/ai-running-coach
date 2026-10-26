"""A stand-in for the RQ queue that keeps RQ 2.x's job-id semantics (#1136).

RQ only rejects a repeated `job_id` when the enqueue passes `unique=True`
(raising `DuplicateJobError`); without it the second enqueue overwrites the job
hash and pushes the id onto the queue again, so the work runs twice. A
`MagicMock` queue cannot show that difference, so the duplicate-enqueue tests use
this instead and assert on what would actually run.
"""

from rq.exceptions import DuplicateJobError


class UniqueAwareQueue:
    def __init__(self) -> None:
        self.job_ids: set[str] = set()
        # One entry per job that would run: (func, args, kwargs).
        self.runs: list[tuple] = []

    def _add(self, func, args, kwargs) -> None:
        job_id = kwargs.get("job_id")
        if kwargs.get("unique") and job_id in self.job_ids:
            raise DuplicateJobError(f"Job {job_id} already exists")
        if job_id is not None:
            self.job_ids.add(job_id)
        self.runs.append((func, args, kwargs))

    def enqueue(self, func, *args, **kwargs):
        self._add(func, args, kwargs)

    def enqueue_in(self, _delay, func, *args, **kwargs):
        self._add(func, args, kwargs)
