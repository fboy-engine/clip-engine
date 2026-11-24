"""Minimal GitHub REST client for the campaign loop (stdlib only).

Uses the workflow's own GITHUB_TOKEN (permissions: actions write + read): it dispatches clip.yml and the
loop's own successor, polls runs and reads the clip artifact. The token never reaches a log or an error."""
import io
import json
import os
import urllib.error
import urllib.request
import zipfile

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class GitHub:
    def __init__(self, token=None, repo=None, http=None, raw=None):
        self.token = (token or os.environ.get("GITHUB_TOKEN") or "").strip()
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY") or "fboy-engine/clip-engine"
        self._http = http or self._real_http
        self._raw = raw or self._real_raw

    def _scrub(self, s):
        s = str(s)
        return s.replace(self.token, "***") if self.token else s

    def _headers(self):
        return {"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "campaign-loop"}

    def _real_http(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw, st = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, st = e.read(), e.code
        except OSError as e:
            raise GitHubError(self._scrub("network: %s" % e))
        try:
            return st, (json.loads(raw.decode() or "{}") if raw else {})
        except ValueError:
            return st, {}

    def _real_raw(self, path):
        """Artifact zip: the API answers with a redirect to blob storage, which must NOT get our token."""
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(API + path, headers=self._headers())
        try:
            opener.open(req, timeout=60)
            raise GitHubError("artifact download: no redirect")
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location")
            if e.code not in (301, 302, 303, 307, 308) or not loc:
                raise GitHubError("artifact download HTTP %s" % e.code)
        try:
            with urllib.request.urlopen(urllib.request.Request(loc, headers={"User-Agent": "campaign-loop"}), timeout=300) as r:
                return r.read()
        except OSError as e:
            raise GitHubError(self._scrub("artifact download: %s" % e))

    def call(self, method, path, body=None, ok=(200,)):
        st, d = self._http(method, path, body)
        if st not in ok:
            raise GitHubError("GitHub %s %s -> HTTP %s" % (method, path.split("?")[0].rsplit("/", 1)[-1], st))
        return d

    # ---- workflows
    def dispatch(self, workflow, inputs, ref="main"):
        self.call("POST", "/repos/%s/actions/workflows/%s/dispatches" % (self.repo, workflow),
                  {"ref": ref, "inputs": {k: str(v) for k, v in inputs.items()}}, ok=(204,))

    def list_runs(self, workflow, since_iso):
        d = self.call("GET", "/repos/%s/actions/workflows/%s/runs?event=workflow_dispatch&per_page=30&created=%%3E%%3D%s"
                      % (self.repo, workflow, since_iso))
        return d.get("workflow_runs", [])

    def get_run(self, run_id):
        return self.call("GET", "/repos/%s/actions/runs/%s" % (self.repo, run_id))

    def failed_step(self, run_id):
        d = self.call("GET", "/repos/%s/actions/runs/%s/jobs?filter=latest" % (self.repo, run_id))
        for j in d.get("jobs", []):
            for s in j.get("steps", []):
                if s.get("conclusion") == "failure":
                    return s.get("name", "?")
        return ""

    # ---- artifacts
    def artifact_zip(self, run_id, prefix):
        d = self.call("GET", "/repos/%s/actions/runs/%s/artifacts?per_page=50" % (self.repo, run_id))
        for a in d.get("artifacts", []):
            if a.get("name", "").startswith(prefix) and not a.get("expired"):
                return zipfile.ZipFile(io.BytesIO(self._raw("/repos/%s/actions/artifacts/%s/zip" % (self.repo, a["id"]))))
        return None
