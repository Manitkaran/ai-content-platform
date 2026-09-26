// Thin JS/TS client for the AI Content Platform (M5.3; extended by CR-029).
//
// A wrapper over the public REST API (D3): it POSTs /transcribe (and /batch),
// polls GET /jobs/{id}, edits segments, downloads subtitles, and manages prompt
// templates. No AI, no capability the API lacks. Uses the global `fetch` and
// `FormData`/`Blob` available in Node >= 18 and browsers.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class Client {
  /**
   * @param {string} baseUrl e.g. "http://localhost:8000"
   * @param {{timeoutMs?: number}} [opts]
   */
  constructor(baseUrl, opts = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.timeoutMs = opts.timeoutMs ?? 30000;
  }

  /**
   * Submit media for transcription.
   * @param {Blob|File} file
   * @param {import(".").TranscribeOptions} [options]
   * @returns {Promise<{id: string, status: string}>}
   */
  async transcribe(file, options = {}) {
    const form = new FormData();
    form.append("file", file, options.filename ?? "upload");
    this.#appendOptions(form, options);
    return this.#request("/transcribe", { method: "POST", body: form });
  }

  /**
   * Submit many files at once (CR-019). Returns the per-file {results:[…]} body;
   * each row is an accepted {filename,id,status} or a rejected {filename,error}.
   * @param {Array<Blob|File>} files
   * @param {import(".").TranscribeOptions} [options] applied to every file
   * @returns {Promise<{results: Array<object>}>}
   */
  async transcribeBatch(files, options = {}) {
    const form = new FormData();
    for (const [i, f] of files.entries()) {
      // CR-029: reuse the per-file name when present, else index them uniquely.
      form.append("files", f, f.name ?? `${options.filename ?? "upload"}-${i}`);
    }
    this.#appendOptions(form, options);
    return this.#request("/transcribe/batch", { method: "POST", body: form });
  }

  /**
   * Fetch a job's current status/result.
   * @param {string} jobId
   */
  async getJob(jobId) {
    return this.#request(`/jobs/${encodeURIComponent(jobId)}`);
  }

  /**
   * Poll until the job is completed or failed (or the timeout elapses).
   * @param {string} jobId
   * @param {{intervalMs?: number, maxMs?: number}} [opts]
   */
  async waitForResult(jobId, opts = {}) {
    const intervalMs = opts.intervalMs ?? 1000;
    const deadline = Date.now() + (opts.maxMs ?? 600000);
    while (Date.now() < deadline) {
      const job = await this.getJob(jobId);
      if (job.status === "completed" || job.status === "failed") return job;
      await new Promise((r) => setTimeout(r, intervalMs));
    }
    throw new Error(`timed out waiting for job ${jobId}`);
  }

  /**
   * Replace a completed job's subtitle segments and re-derive its text (CR-018).
   * @param {string} jobId
   * @param {Array<import(".").Segment>} segments
   * @returns {Promise<import(".").Job>}
   */
  async editSegments(jobId, segments) {
    return this.#request(`/jobs/${encodeURIComponent(jobId)}/segments`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segments }),
    });
  }

  /**
   * Download the rendered transcript/subtitle file as text (CR-018/CR-020).
   * @param {string} jobId
   * @param {import(".").OutputFormat} [format] override the job's own format
   * @returns {Promise<string>} the rendered file body
   */
  async downloadSubtitle(jobId, format) {
    const q = format ? `?format=${encodeURIComponent(format)}` : "";
    return this.#requestText(`/jobs/${encodeURIComponent(jobId)}/subtitle${q}`);
  }

  /** List saved prompt templates (CR-022). @returns {Promise<Array<import(".").Template>>} */
  async listTemplates() {
    return this.#request("/templates");
  }

  /**
   * Create or update a prompt template (CR-022).
   * @param {import(".").Template} template
   * @returns {Promise<import(".").Template>}
   */
  async saveTemplate(template) {
    return this.#request("/templates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(template),
    });
  }

  /** Delete a prompt template (CR-022; idempotent). @param {string} name */
  async deleteTemplate(name) {
    await this.#requestText(`/templates/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
  }

  /**
   * Convenience URL for downloading the rendered subtitle/transcript file.
   * @param {string} jobId
   * @param {import(".").OutputFormat} [format]
   */
  subtitleUrl(jobId, format) {
    const q = format ? `?format=${encodeURIComponent(format)}` : "";
    return `${this.baseUrl}/jobs/${encodeURIComponent(jobId)}/subtitle${q}`;
  }

  // CR-029: only append fields the caller set, so the multipart body stays minimal
  // and the server's defaults apply for anything omitted.
  #appendOptions(form, options) {
    form.append("language", options.language ?? "auto");
    form.append("translate", String(options.translate ?? false));
    form.append("output_format", options.output_format ?? "text");
    if (options.target_language) form.append("target_language", options.target_language);
    if (options.prompt) form.append("prompt", options.prompt);
    if (options.template) form.append("template", options.template);
  }

  async #request(path, init = {}) {
    const res = await this.#fetch(path, init);
    const text = await res.text();
    const body = text ? JSON.parse(text) : {};
    if (!res.ok) {
      throw new ApiError(body.detail ?? `HTTP ${res.status}`, res.status);
    }
    return body;
  }

  // CR-029: for endpoints whose body is not JSON (the subtitle download) or empty
  // (204 on template delete) — return the raw text and never JSON.parse it.
  async #requestText(path, init = {}) {
    const res = await this.#fetch(path, init);
    const text = await res.text();
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        detail = JSON.parse(text).detail ?? detail;
      } catch {
        /* body was not JSON — keep the status message */
      }
      throw new ApiError(detail, res.status);
    }
    return text;
  }

  async #fetch(path, init) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), this.timeoutMs);
    try {
      return await fetch(this.baseUrl + path, { ...init, signal: ctrl.signal });
    } finally {
      clearTimeout(t);
    }
  }
}

export default Client;
