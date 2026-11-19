---
name: study-formats
description: Reverse engineer a game's file formats in the study workspace - identify files by signature and entropy, look at bounded hexdumps and strings, compare two versions of a file to find the fields that changed, recover the layout from the code that reads or writes it, write a reader (dissect.cstruct, or an ImHex pattern) and prove it with a byte-for-byte round-trip. Saves, configs, data tables, custom containers.
when_to_use: The user wants to understand a file the game reads or writes. Examples - "what format is this file", "how is the save file structured", "decode this .bin", "write a parser for the save", "what changed in the save after I leveled up", "que formato é esse arquivo do jogo", "como o save é organizado", "decodifica esse .bin", "escreve um leitor do save", "o que mudou no save".
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# Study a file format

Where you are: the session context names the study workspace, if any (`gst status` shows it).

Goal: a layout you can prove - a reader that parses every sample and writes it back byte for byte.
Reply in pt-BR; notes in pt-BR.

## 1. Identify

```
gst fmt id "<file or glob>"          # signature, size, entropy (paths relative to the install work too)
gst fmt scan "<folder>"              # many unknown files: per extension, kinds, sizes, entropy
gst fmt entropy "<file>"             # regions: text/structure vs compressed or encrypted
gst fmt hex "<file>" --length 128    # at most 512 bytes per call; --offset 0x.. (negative: from the end)
gst fmt strings "<file>" --grep "<regex>"
```

- High entropy everywhere (7.2+ bits/byte): compressed or encrypted. Look for a compression header
  (zlib `78 9c`, zstd, lz4) before thinking of encryption; encryption follows guardrails rule 4.
- Text formats (JSON, YAML, INI, XML) need no reader: read them in place.
- Engine containers (Unity, Unreal, Godot, audio banks) belong to the study-assets skill.

## 2. Find the code that reads or writes it

Usually faster than guessing, and it is the evidence for each field:

```
gst q strings "<file name or extension>"   # where the path is built
gst q callers "<Loader.Load>"               # who reads it
gst q show "<Serializer.Write>"             # field order, types, version checks
```

A serializer library (MemoryPack, protobuf, MessagePack, BinaryFormatter, Unreal GVAS properties) means
a public format: name it in the note and work from its specification plus the game's types.

## 3. Compare versions

Change one thing in the game (spend gold, take damage), save, and compare:

```
gst fmt diff "<before>" "<after>"    # ranges that changed; a size change means inserted or removed data
```

Several saves with known values pin a field down; keep them in `dumps/` with a line on what changed.

## 4. Write the reader and prove it

```
gst fmt new-reader <name> --sample "<file>"          # notes/formats/<name>.py (dissect.cstruct) + <name>.md
gst fmt roundtrip notes/formats/<name>.py "<samples>"  # every file must come back identical
gst fmt pattern notes/formats/<name>.hexpat "<file>"  # optional ImHex pattern: decoded tree as JSON in dumps/formats/
```

- Map from the start of the file and keep the unknown tail as raw bytes (`rest`), so the round-trip holds
  while you work; `describe()` shows decoded fields and `unmapped_bytes`.
- Earlier fields drive later ones: `char name[name_len]`, `Entity items[count]`.
- Checksums: recompute them in `describe()` (`zlib.crc32`, ...) to confirm which bytes they cover.
- ImHex (`gst tools path imhex`) opens the file and the pattern interactively when a picture helps.

## 5. Record

`notes/formats/<name>.md`: samples, the layout table (offset, size, type, field, evidence), who reads and
writes it in the code (`path:line`), what is verified and what is not. Key facts go to FACTS.md.

## Done when

The reader round-trips every sample byte for byte, the note explains each field with evidence, and what
remains raw is listed as unverified.

## Common problems

| Symptom | Fix |
|---|---|
| `DIFFERS at 0x..` | a size or count is off just before that offset: `gst fmt hex <file> --offset 0x..` |
| ERROR from the reader | the message names the struct field; check endianness and length fields |
| a negative string length | UTF-16 text (Unreal FString convention): 2 bytes per character |
| high entropy after a small header | compressed payload: decompress in `parse` and recompress identically in `dump`, or keep it raw |
| no structure and no compression header | likely encrypted: rule 4 (published key only); study it from the code instead |
| `dissect.cstruct` is missing | `gst tools install dissect-cstruct` |
