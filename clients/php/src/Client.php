<?php

declare(strict_types=1);

namespace AiContentPlatform;

/**
 * Thin PHP client for the AI Content Platform (M5.2; extended by CR-029).
 *
 * A wrapper over the public REST API (D3) — it contains no AI and adds no
 * capability the API lacks. It calls the transcription, job, subtitle, and
 * template endpoints exactly as any other caller would.
 *
 * Example:
 *   $client = new Client('http://localhost:8000');
 *   $job = $client->transcribe('/path/to/audio.mp3', ['language' => 'auto']);
 *   $result = $client->waitForResult($job['id']);
 *   echo $result['result']['text'];
 */
final class Client
{
    public function __construct(
        private string $baseUrl,
        private int $timeout = 30,
    ) {
        $this->baseUrl = rtrim($baseUrl, '/');
    }

    /**
     * Submit a media file. Returns the decoded {id, status} body.
     *
     * @param array{language?:string,translate?:bool,target_language?:string,output_format?:string,prompt?:string,template?:string} $options
     * @return array<string,mixed>
     */
    public function transcribe(string $filePath, array $options = []): array
    {
        if (!is_readable($filePath)) {
            throw new \InvalidArgumentException("file not readable: {$filePath}");
        }
        $post = $this->optionFields($options);
        $post['file'] = new \CURLFile($filePath);

        $ch = curl_init("{$this->baseUrl}/transcribe");
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $post,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $this->timeout,
        ]);
        return $this->send($ch);
    }

    /**
     * Submit many files at once (CR-019). Returns the per-file {results:[…]} body;
     * each row is an accepted {filename,id,status} or a rejected {filename,error}.
     *
     * @param list<string> $filePaths
     * @param array{language?:string,translate?:bool,target_language?:string,output_format?:string,prompt?:string,template?:string} $options
     * @return array<string,mixed>
     */
    public function transcribeBatch(array $filePaths, array $options = []): array
    {
        $post = $this->optionFields($options);
        foreach (array_values($filePaths) as $i => $path) {
            if (!is_readable($path)) {
                throw new \InvalidArgumentException("file not readable: {$path}");
            }
            // CR-029: PHP's curl needs distinct array keys to send repeated "files[]".
            $post["files[{$i}]"] = new \CURLFile($path);
        }

        $ch = curl_init("{$this->baseUrl}/transcribe/batch");
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $post,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $this->timeout,
        ]);
        return $this->send($ch);
    }

    /**
     * Fetch a job's current status/result.
     *
     * @return array<string,mixed>
     */
    public function getJob(string $jobId): array
    {
        $ch = curl_init("{$this->baseUrl}/jobs/" . rawurlencode($jobId));
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $this->timeout,
        ]);
        return $this->send($ch);
    }

    /**
     * Poll until the job reaches a terminal state (completed/failed) or times out.
     *
     * @return array<string,mixed>
     */
    public function waitForResult(string $jobId, float $intervalSeconds = 1.0, int $maxSeconds = 600): array
    {
        $deadline = microtime(true) + $maxSeconds;
        while (microtime(true) < $deadline) {
            $job = $this->getJob($jobId);
            if (in_array($job['status'] ?? '', ['completed', 'failed'], true)) {
                return $job;
            }
            usleep((int) ($intervalSeconds * 1_000_000));
        }
        throw new \RuntimeException("timed out waiting for job {$jobId}");
    }

    /**
     * Replace a completed job's subtitle segments and re-derive its text (CR-018).
     *
     * @param list<array{start:float,end:float,text:string}> $segments
     * @return array<string,mixed>
     */
    public function editSegments(string $jobId, array $segments): array
    {
        $ch = curl_init("{$this->baseUrl}/jobs/" . rawurlencode($jobId) . "/segments");
        curl_setopt_array($ch, [
            CURLOPT_CUSTOMREQUEST => 'PUT',
            CURLOPT_POSTFIELDS => json_encode(['segments' => $segments]),
            CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $this->timeout,
        ]);
        return $this->send($ch);
    }

    /**
     * Download the rendered transcript/subtitle file as a string (CR-018/CR-020).
     * Pass $format (txt/srt/vtt/json) to override the job's own format.
     */
    public function downloadSubtitle(string $jobId, ?string $format = null): string
    {
        $url = "{$this->baseUrl}/jobs/" . rawurlencode($jobId) . "/subtitle";
        if ($format !== null) {
            $url .= '?format=' . rawurlencode($format);
        }
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $this->timeout,
        ]);
        return $this->sendRaw($ch);
    }

    /**
     * List saved prompt templates (CR-022).
     *
     * @return array<int,array<string,mixed>>
     */
    public function listTemplates(): array
    {
        $ch = curl_init("{$this->baseUrl}/templates");
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $this->timeout,
        ]);
        return $this->send($ch);
    }

    /**
     * Create or update a prompt template (CR-022).
     *
     * @param array{name:string,prompt:string,description?:string} $template
     * @return array<string,mixed>
     */
    public function saveTemplate(array $template): array
    {
        $ch = curl_init("{$this->baseUrl}/templates");
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => json_encode($template),
            CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $this->timeout,
        ]);
        return $this->send($ch);
    }

    /** Delete a prompt template (CR-022; idempotent — deleting a missing name is fine). */
    public function deleteTemplate(string $name): void
    {
        $ch = curl_init("{$this->baseUrl}/templates/" . rawurlencode($name));
        curl_setopt_array($ch, [
            CURLOPT_CUSTOMREQUEST => 'DELETE',
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $this->timeout,
        ]);
        $this->sendRaw($ch);  // 204 No Content
    }

    /**
     * Build the shared transcription form fields (CR-029). Only sends the optional
     * ones when set, so the server's defaults apply for anything omitted.
     *
     * @param array<string,mixed> $options
     * @return array<string,mixed>
     */
    private function optionFields(array $options): array
    {
        $post = [
            'language' => $options['language'] ?? 'auto',
            'translate' => ($options['translate'] ?? false) ? 'true' : 'false',
            'output_format' => $options['output_format'] ?? 'text',
        ];
        foreach (['target_language', 'prompt', 'template'] as $key) {
            if (!empty($options[$key])) {
                $post[$key] = (string) $options[$key];
            }
        }
        return $post;
    }

    /**
     * Execute a request expecting a JSON body; decode it (or throw ApiException on 4xx/5xx).
     *
     * @return array<string,mixed>
     */
    private function send(\CurlHandle $ch): array
    {
        $body = $this->sendRaw($ch);
        $decoded = json_decode($body, true);
        return is_array($decoded) ? $decoded : [];
    }

    /**
     * Execute a request and return the raw response body — used for non-JSON
     * responses (the subtitle download) and empty ones (204). Throws ApiException
     * on a 4xx/5xx, surfacing the JSON `detail` when the error body carries one.
     */
    private function sendRaw(\CurlHandle $ch): string
    {
        $body = curl_exec($ch);
        if ($body === false) {
            $err = curl_error($ch);
            curl_close($ch);
            throw new \RuntimeException("HTTP request failed: {$err}");
        }
        $status = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
        curl_close($ch);
        $body = (string) $body;

        if ($status >= 400) {
            $decoded = json_decode($body, true);
            $detail = is_array($decoded) ? ($decoded['detail'] ?? $body) : $body;
            throw new ApiException("API error {$status}: {$detail}", $status);
        }
        return $body;
    }
}
