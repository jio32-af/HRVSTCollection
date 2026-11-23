# HRVSTCompress

Standalone, dependency-free data compression and information-theory laboratory.
Open `index.html` in any browser — no server, no build, no libraries.

## What it does

- **Huffman coding** — min-heap priority queue, binary prefix tree, and
  variable-length bit encodings, all implemented from scratch in vanilla JS.
- **LZW dictionary coding** — 256-entry byte alphabet that learns longer
  phrases as it scans; encoder and decoder both from scratch.
- **Interactive Huffman tree canvas** — tidy layout with character frequencies
  and 0/1 edge weights.
- **Color-coded bitstream viewer** — every symbol's compressed bits in its own
  color, with a matching code-table legend.
- **Metrics dashboard** — raw bits, compressed bits, compression ratios,
  Shannon entropy theoretical limit, average code length, and exact
  encode→decode round-trip verification for both codecs.

All computation is byte-level UTF-8 (hand-rolled encoder/decoder, zero deps),
so any text round-trips exactly. LZW sizes assume classic fixed 12-bit code
framing, stated in the page footer.
