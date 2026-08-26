# Ego Lite 0.4.6.13 clean-room analysis

This directory records independently recovered interface facts used to design
Browser Lite. Vendor JavaScript recovered verbatim from the installed app is
kept in a separate local evidence archive and is not committed to this repo.
Inferred C++/Mojo names and types are not presented as vendor originals.

## Snapshot and scope

- Installed app: `/Applications/ego lite.app`
- Analyzed bundle version: `0.4.6.13`
- Running app observed during read-only probes: `0.4.6.12`
- Architecture: arm64 Mach-O
- CLI SHA-256: `6ead1ce64221f15998d9a6f1864d94b34b2c313f9f9a42632b7293de15c32299`
- Framework SHA-256: `11e3ef3cd6973c8802db82926c86ad8e696747ea8dfeceb20272c787f77e58e3`

Methods used: `strings`, `llvm-nm`, `llvm-objdump`, launchd/process
inspection, angr CFG/decompilation, Chromium DataPack parsing, and read-only
calls through the shipped `ego-browser nodejs` command. The probes did not
create, claim, delete, complete, or navigate task spaces.

## Recovered shipped JavaScript

The Framework's `Resources/resources.pak` is a Chromium DataPack v5 containing
5,690 resources and 365 aliases. Several resources are gzip-compressed
JavaScript, not native-machine-code decompiler output:

| Resource | Decoded bytes | Contents |
| --- | ---: | --- |
| `58860` | 378,119 | formatted Node browser SDK, comments, function/local names and JSDoc contracts |
| `58492` | 22,564 | minified NativeBridge/onboarding/profile-import client |
| `58760` | 7,225 | minified frontend CDP/task-detail transport in 0.4.6.13 |

Resource `58860` has the same SHA-256 in 0.4.6.12 and 0.4.6.13
(`b8b0f3eaf84d09620285a79cb057e455c55390f16eb541d19ea7ccacde6c7fb1`).
Resource `58492` is also identical; `58760` changed between those releases.
The recovered artifacts and hashes live at
`/Users/xy718/Documents/Codex/2026-08-13/ego-lite-recovery/`.
No DataPack resource contains `sourcesContent`; the two UI resources therefore
remain the exact distributed minified bundles rather than reconstructed
pre-minification TypeScript.

The included `unpack_chromium_pak.mjs` script reproduces DataPack enumeration,
search and extraction without relying on Ego binaries at runtime.

## Recovered architecture

```text
ego-browser (Rust clap parser + C++ Mojo client)
        |
        | bootstrap_look_up("com.citrolabs.ego.lite.ego-browser")
        | Mojo invitation/message pipe
        v
ego browser process
  EgoCliBootstrap -> EgoCliBridge host
        |
        +-- onboarding / upgrade / profile import
        |
        +-- launches ego Helper (Node)
              --utility-sub-type=ego.mojom.NodeService
              --service-sandbox-type=none
                    |
                    +-- NodeService / NodeRuntime
                    +-- NodeCdpChannel / NodeCdpChannelClient
                    +-- native V8 binding named `ego`
                              |
                              v
                    task-space controller + WebContents/CDP
```

The CLI does not expose an ordinary TCP server. The running browser registered
the launchd endpoint `com.citrolabs.ego.lite.ego-browser`. A copied and ad-hoc
re-signed CLI could not connect, which is consistent with peer code-identity
validation in the rendezvous path.

## Confirmed native Node binding

`Object.getOwnPropertyNames(ego)` and the Framework registration table agree on
the following functions:

| Function | Recovered contract |
| --- | --- |
| `createTab` | `createTab(url: string)` |
| `listTabs` | no arguments |
| `listTaskSpaces` | no arguments |
| `deleteSpaces` | `{scope: "all"|"task"}` or `{ids: number[]}` |
| `listProfiles` | no arguments |
| `snapshot` | options include `interactiveOnly`, `includeActionMarks`, `includeStableLocator`, `maxResultLength`, `refs` |
| `createTaskSpace` | `(name: string, profileId?: string)` |
| `claimTaskSpace` | `(id: number, name?: string)` |
| `closeTaskSpace` | no arguments |
| `useTaskSpace` | `(id: number)`; observed to set/return the ID before later validation |
| `animationHighlightMouseToPosition` | `(x: number, y: number)` |
| `handOffTaskSpace` | no arguments |
| `takeOverTaskSpace` | no arguments |
| `completeTaskSpace` | no arguments |
| `markTaskSpaceError` | `(message: string)` |
| `setAgentTaskState` | `(state: string)` |
| `getBrowserVersion` | no arguments |
| `sendCDPMessage` | `(message: string)` where the string contains a CDP JSON message |

The binary's help example says `handOffTask()`, but runtime reflection confirms
that only `handOffTaskSpace` is exported. `onCDPMessage` and
`onSendCDPMessageError` occur in the native registration path but are not own
properties of the exposed `ego` object; they are treated as internal callback
hooks.

Observed task-space JSON fields:

```json
{
  "createdBy": "agent",
  "id": 10,
  "name": "example-task",
  "ownership": "agent",
  "profileId": "Default",
  "profileName": "Local profile",
  "recentTabTitles": ["Example"],
  "taskId": "example-task"
}
```

The recovered SDK documents all three public ownership values: `agent`,
`agentDelegatedToUser`, and `user`. `agentDelegatedToUser` remains agent-owned,
but control is temporarily with the user; browser commands stop with
`EGO_TASK_SPACE_USER_IN_CONTROL` until explicit takeover.

The SDK also confirms a 15-second raw-CDP response timeout, a 2-second attached
target-session cache, one retry after session loss, and agent-friendly snapshot
defaults (`full_page`, action marks, stable locators). Its control helpers never
take over automatically while the user is in control.

## Stable error surface

The Framework contains these public error codes:

```text
EGO_BROWSER_UNAVAILABLE
EGO_CDP_CHANNEL_UNAVAILABLE
EGO_CDP_SEND_FAILED
EGO_INVALID_ARGUMENT
EGO_INVALID_RESULT_PAYLOAD
EGO_OPERATION_FAILED
EGO_PROFILE_NOT_FOUND
EGO_RESULT_CONVERSION_FAILED
EGO_SNAPSHOT_FAILED
EGO_TASK_HOST_DISCONNECTED
EGO_TASK_SPACE_INACTIVE
EGO_TASK_SPACE_NOT_FOUND
EGO_TASK_SPACE_NOT_SELECTED
EGO_TASK_SPACE_UNAVAILABLE
EGO_TASK_SPACE_USER_IN_CONTROL
EGO_WEB_CONTENTS_UNAVAILABLE
```

Argument failures observed at the JavaScript surface are `TypeError` instances
with detailed examples. Browser/task failures use the stable codes above.

## CLI Mojo evidence

The CLI binds `ego.mojom.EgoCliBootstrap`, then `ego.mojom.EgoCliBridge`.
Generated proxy serializers contain ordinals `0-10`, `18-20` with request
payload sizes shown in `ego_cli_bridge_inferred.mojom`.

Two calls are identified strongly from their command handlers:

- ordinal `2`: list importable browsers/profiles; no request fields
- ordinal `3`: execute profile import; one encoded import-request collection
  plus default-browser and overwrite flags

Other names remain intentionally reserved because a payload size alone is not
enough to reconstruct the original method signature. The CLI command surface
shows that the remaining calls support onboarding, upgrade, and Node runtime
startup/evaluation.

## Browser/profile import implementation evidence

Embedded source paths identify a dedicated browser-side import subsystem:

```text
ego/browser/data_import/browser_import_for_cli.cc
ego/browser/data_import/browser_syncer.cc
ego/browser/data_import/chromium/chromium_import.cc
ego/browser/data_import/chromium/chromium_import_copy.cc
ego/browser/data_import/chromium/chromium_import_internal.cc
ego/browser/data_import/chromium/chromium_import_profile_display.cc
ego/browser/data_import/chromium/chromium_import_profile_recovery.cc
```

The shipped CLI accepts Chrome, Edge, and Brave sources, multiple profile
blocks, `--overwrite`, and `--no-default`. A read-only `import list` call
returned browser source, display name, default-browser state, and profile
directory/display/default fields.

The shipped NativeBridge resource additionally exposes the real import-stage
model: preflight, planning, file copy, bookmarks, passwords, cookies, Web Data,
sessions, extensions, profile metadata, finalizing, and finished. Its 35 result
codes distinguish database-version/locking problems from keychain, decrypt and
re-encryption failures. This proves that passwords, cookies, sessions and
extensions are first-class importer stages rather than only CLI labels.

## What is recovered, reconstructed, and still missing

### Recovered with high confidence

- Process boundaries and launch arguments
- Mach bootstrap endpoint and Mojo interface names
- CLI method ordinals and request payload sizes
- The shipped Node SDK JavaScript, including comments, local names and JSDoc
- Complete native Node function list, argument validation text, option keys,
  task-space result shape, ownership values, and stable error codes
- NativeBridge import stages, failure codes and controller method names
- Relevant internal C++ source path names
- Profile importer CLI behavior and detected-source response shape

### Clean-room equivalents implemented in Browser Lite

- `src/task-space-manager.mjs`: persistent task-space state machine, ownership
  handoff/takeover, lifecycle, tabs, accessibility snapshot, CDP, mouse marker,
  dispatch allowlist, and compatible error codes
- `src/electron-runtime.mjs`: raw CDP command path against the selected
  `WebContentsView`
- `src/node-agent.mjs`: authenticated remote `task_space` action
- `test/task-space-manager.test.mjs`: transition and protocol contract tests

### Still not present as original repository source

- Original C++ formatting, comments, local variable names, typedef names, and
  template types removed by optimization
- Original GN/Ninja files and conditional build graph
- Exact `.mojom` source names/types for every CLI and Node service method
- A complete Chromium-fork Git diff against its private base commit
- Unit tests and server source not shipped in the app bundle

These missing artifacts can be independently reimplemented, but that result is
new source compatible with observed behavior, not a byte-for-byte recovery of
the vendor repository. The recovered JavaScript above is different: it is the
actual distributed artifact and does not depend on decompiler guesses.

## Reproduction

The core read-only commands are:

```bash
xcrun llvm-nm -nm "/Applications/ego lite.app/Contents/Frameworks/ego Framework.framework/Versions/0.4.6.13/Helpers/ego-browser"
xcrun llvm-objdump -d --arch-name=arm64 "/Applications/ego lite.app/Contents/Frameworks/ego Framework.framework/Versions/0.4.6.13/Helpers/ego-browser"
strings -a -t x "/Applications/ego lite.app/Contents/Frameworks/ego Framework.framework/Versions/0.4.6.13/ego Framework"
launchctl print "gui/$(id -u)"
node unpack_chromium_pak.mjs \
  "/Applications/ego lite.app/Contents/Frameworks/ego Framework.framework/Versions/0.4.6.13/Resources/resources.pak" \
  --extract 58860 /tmp/ego-node-sdk.js --decompress gzip
```

angr 9.2 generated a normalized CFG with 13,475 functions for the CLI. Retained
bridge symbols include:

```text
0x100161f40 serde_json_lenient$cxxbridge1$194$decode_json
0x10016853c ego_cli$cxxbridge1$194$parse_invocation
0x100168ebc ego_cli$cxxbridge1$194$parse_nodejs_options
```

Dynamic debugger attachment to the hardened, signed CLI was denied by macOS;
the analysis therefore relies on static decompilation and the application's
own supported read-only command surface.
