# Code Signing Policy

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

## What is signed

The Windows executable `faster-whisper-xxl-gui.exe` published on the [Releases](https://github.com/cbro33/Faster-Whisper-XXL-GUI/releases) page. Nothing else is signed with this certificate, and no other project is signed with it.

## How builds are produced

Releases are built by GitHub Actions from the tagged commit, on GitHub hosted runners, using the workflow in [.github/workflows/ci.yml](.github/workflows/ci.yml). Every build runs the test suite and starts the packaged executable before the artifact is submitted for signing.

A SHA256 checksum is published alongside the binary. It is generated after signing, so it describes the file that users actually download.

## Roles

The project has a single maintainer, [cbro33](https://github.com/cbro33), who acts as committer, reviewer and approver.

Anyone may open issues and pull requests. Contributed changes are reviewed and merged by the maintainer, and only a commit that has been merged and tagged is ever built and signed.

## Private key

The private key for the release certificate is generated and held by SignPath Foundation in a hardware security module. It is not stored in this repository, on any maintainer machine, or in any CI secret, and the project cannot export it.

## Privacy

The application does not collect or transmit user data. Transcription runs locally on the user's machine. The application makes outbound requests only to download the transcription engine, FFmpeg, speech recognition models, and its own updates.
