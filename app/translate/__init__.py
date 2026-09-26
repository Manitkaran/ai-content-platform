"""The ``Translator`` seam — arbitrary source→target machine translation (CR-025 / D25).

A post-transcription stage: Whisper produces text in the source language; this seam
renders that text (and each segment's text) into a caller-named ``target_language``.
It is a *seam* like ``Transcriber``/``ObjectStorage`` — a ``contract`` + a ``registry``
selecting a driver by config, so feature code depends on the contract, never a driver.
The MT library (Argos Translate) is imported only under ``drivers/`` (import boundary).
"""
