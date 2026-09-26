// Type declarations for the thin JS/TS client (M5.3; extended by CR-029).

export type OutputFormat = "text" | "srt" | "vtt" | "json";
export type JobStatus = "queued" | "running" | "completed" | "failed";

export interface Segment {
  start: number;
  end: number;
  text: string;
}

export interface TranscribeResult {
  text: string;
  detected_language: string;
  segments: Segment[];
  output_format: OutputFormat;
}

export interface Job {
  id: string;
  status: JobStatus;
  original_filename: string;
  detected_language?: string | null;
  result?: TranscribeResult | null;
  error?: { code: string; message: string } | null;
}

export interface TranscribeOptions {
  language?: string;
  /** Translate to English, Whisper-native. Independent of `target_language`. */
  translate?: boolean;
  /** Translate the transcript into this ISO code via the MT seam (CR-025). Not "auto". */
  target_language?: string;
  output_format?: OutputFormat;
  /** Initial prompt biasing vocabulary — names, jargon (CR-022). */
  prompt?: string;
  /** Name of a saved prompt template to apply (CR-022). `prompt` wins if both set. */
  template?: string;
  filename?: string;
}

/** A per-file row from POST /transcribe/batch (CR-019). */
export interface BatchItemResult {
  filename: string;
  id?: string;
  status?: JobStatus;
  error?: { code: string; message: string };
}

export interface BatchResponse {
  results: BatchItemResult[];
}

/** A saved prompt template (CR-022). */
export interface Template {
  name: string;
  prompt: string;
  description?: string;
}

export class ApiError extends Error {
  status: number;
}

export class Client {
  constructor(baseUrl: string, opts?: { timeoutMs?: number });
  transcribe(file: Blob | File, options?: TranscribeOptions): Promise<{ id: string; status: JobStatus }>;
  transcribeBatch(files: Array<Blob | File>, options?: TranscribeOptions): Promise<BatchResponse>;
  getJob(jobId: string): Promise<Job>;
  waitForResult(jobId: string, opts?: { intervalMs?: number; maxMs?: number }): Promise<Job>;
  editSegments(jobId: string, segments: Segment[]): Promise<Job>;
  downloadSubtitle(jobId: string, format?: OutputFormat): Promise<string>;
  listTemplates(): Promise<Template[]>;
  saveTemplate(template: Template): Promise<Template>;
  deleteTemplate(name: string): Promise<void>;
  subtitleUrl(jobId: string, format?: OutputFormat): string;
}

export default Client;
