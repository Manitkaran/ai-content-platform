<?php

declare(strict_types=1);

namespace AiContentPlatform;

/** Raised when the API returns a 4xx/5xx response (M5.2). */
final class ApiException extends \RuntimeException
{
    public function __construct(string $message, private int $statusCode)
    {
        parent::__construct($message);
    }

    public function getStatusCode(): int
    {
        return $this->statusCode;
    }
}
