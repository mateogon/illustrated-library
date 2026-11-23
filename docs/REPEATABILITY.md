# Does the same book get the same illustrations twice?

We ran text selection three times on the complete 1927 Spanish translation of *Alice’s Adventures in Wonderland*, with the same EPUB, model alias, prompts, 91 candidate paragraph endings, preceding context and spacing rules. The first run came from the production archive. An offline replay verified all 202 of its request fingerprints. The other two runs used independent provider responses and retained input caching. This experiment generated no images.

| Text pass | Valid proposals | Approved scene cards | Selected cuts | Median gap | Longest internal gap |
|---|---:|---:|---:|---:|---:|
| Spanish 1 | 58 | 50 | 21 | 4 pages | 8 pages |
| Spanish 2 | 57 | 50 | 22 | 4 pages | 7 pages |
| Spanish 3 | 49 | 46 | 20 | 4 pages | 7 pages |

The three passes selected 26 distinct cuts. **Sixteen were selected in all three.** Each run covered all twelve chapters, with no gap longer than eight laboratory pages. A laboratory page groups roughly 300 words and depends on paragraph boundaries. Reader screen size and font settings change displayed pagination.

Even an identical cut can yield another composition. At P0905 one draft emphasized Alice telling her story and another emphasized the Mock Turtle drawing breath as she listened. Spacing can amplify a small change. Restoring one candidate at P0360 to the third run replaced the single placement on page 37 with placements on pages 35 and 40. Every other placement stayed the same.

The previously processed English edition selected 19 cuts in its text pass, with a 4.5-page median gap. Its source has 791 paragraph units and 88 sampled cuts. The Spanish source has 1,139 paragraph units and 91 sampled cuts. The English edition was run once. These data show variation within one Spanish edition and do not isolate the effect of language or translation across editions. The public EPUBs went through a later image review and can have fewer pictures than the text selections above.

The two new runs made 391 requests. Their usage-based estimated cost was **USD 0.2554** in total. Including the historical run yields **USD 0.4285** for these three text passes.
