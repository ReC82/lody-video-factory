"""Sous-titres séparés SRT / VTT : lecture du SRT existant, français corrigé, synchronisation. Aucun appel."""

from __future__ import annotations

import re


from lody.generation import subtitles as sb

SRT = """1
00:00:00,000 --> 00:00:01,869
Si tu découvres le Web3 gaming

2
00:00:04,984 --> 00:00:06,853
mais l’  idée de base est simple

3
00:00:06,853 --> 00:00:09,407
c’ est un grand registre numérique partagé

4
00:00:09,407 --> 00:00:12,000
qu’ il n’ est pas garanti
"""


def test_parse_reads_bom_crlf_and_dot_decimals():
    raw = "﻿1\r\n00:00:01.5 --> 00:00:02.25\r\nBonjour\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nSalut\r\nle monde\r\n"
    cues = sb.parse_srt(raw)
    assert [(c.start_ms, c.end_ms, c.text) for c in cues] == [(1500, 2250, "Bonjour"), (3000, 4000, "Salut\nle monde")]
    assert sb.parse_srt("bloc sans minutage\n\nrien du tout") == []


def test_french_typography_is_fixed_in_the_text_only_never_in_the_timecodes():
    cues, check = sb.build(SRT, "fr-FR", 20_000)
    texts = [c.text for c in cues]
    assert texts[1] == "mais l’idée de base est simple" and texts[2] == "c’est un grand registre numérique partagé"
    assert texts[3] == "qu’il n’est pas garanti"
    assert [(c.start_ms, c.end_ms) for c in cues] == [(0, 1869), (4984, 6853), (6853, 9407), (9407, 12000)]
    assert check.ok and check.issues == ()
    for phrase in ("l’idée", "c’est", "qu’il", "n’est"):
        assert phrase in sb.plain_text(cues)
    assert not re.search(r"[’'] ", sb.plain_text(cues)) and "  " not in sb.plain_text(cues)


def test_other_languages_keep_their_text():
    cues, _ = sb.build("1\n00:00:00,000 --> 00:00:01,000\nl' idea here\n", "en-US", 5000)
    assert cues[0].text == "l' idea here"


def test_srt_is_utf8_with_numbering_and_comma_timestamps():
    cues, _ = sb.build(SRT, "fr-FR", 20_000)
    srt = sb.to_srt(cues)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,869\nSi tu découvres") and srt.endswith("garanti\n\n")
    assert re.findall(r"^\d+$", srt, re.M) == ["1", "2", "3", "4"]
    assert srt.encode("utf-8").decode("utf-8") == srt and "é" in srt and "﻿" not in srt
    assert sb.parse_srt(srt) == cues                                         # aller-retour sans perte


def test_vtt_header_dot_timestamps_language_and_escaping():
    cues = [sb.Cue(1, 0, 1500, "a < b & c"), sb.Cue(2, 3_661_002, 3_662_000, "l’idée")]
    vtt = sb.to_vtt(cues)
    assert vtt.startswith("WEBVTT\nKind: captions\nLanguage: fr\n\n")
    assert "00:00:00.000 --> 00:00:01.500\na &lt; b &amp; c" in vtt and "01:01:01.002 --> 01:01:02.000\nl’idée" in vtt
    assert "," not in vtt.split("\n\n", 1)[1].split("\n")[0]                 # pas de virgule décimale
    assert sb.to_vtt([]) == "WEBVTT\n\n"


def test_sync_last_cue_never_exceeds_the_final_duration():
    cues, check = sb.build(SRT, "fr-FR", 11_000)                             # la vidéo se termine à 11 s
    assert cues[-1].end_ms == 11_000 and check.ok
    assert any("ramenée à la durée de la vidéo" in fix for fix in check.fixes)
    late = SRT + "\n5\n00:00:15,000 --> 00:00:16,000\nAprès la fin\n"
    cues, check = sb.build(late, "fr-FR", 11_000)
    assert all(c.end_ms <= 11_000 for c in cues) and "Après la fin" not in sb.plain_text(cues)
    assert any("après la fin de la vidéo" in fix for fix in check.fixes) and check.ok


def test_within_tolerance_and_exact_end_are_untouched():
    cues, check = sb.build("1\n00:00:00,000 --> 00:00:05,050\nFin\n", "fr", 5_000)   # 50 ms de dépassement : corrigé à 5 000
    assert cues[0].end_ms == 5_000 and check.last_end_ms == 5_000
    cues, check = sb.build("1\n00:00:00,000 --> 00:00:05,000\nFin\n", "fr", 5_000)
    assert check.fixes == () and cues[0].end_ms == 5_000


def test_overlaps_empty_cues_and_disorder_are_repaired():
    raw = "1\n00:00:02,000 --> 00:00:04,000\nDeux\n\n2\n00:00:00,000 --> 00:00:03,000\nUn\n\n3\n00:00:05,000 --> 00:00:05,000\nSans durée\n\n4\n00:00:06,000 --> 00:00:07,000\n\n"
    cues, check = sb.build(raw, "fr", None)
    assert [c.text for c in cues] == ["Un", "Deux"]
    assert cues[0].end_ms <= cues[1].start_ms and check.ok
    assert sb.check([], 5000).issues == ("aucun sous-titre",)


def test_check_flags_what_remains_wrong():
    bad = [sb.Cue(1, 0, 3000, "l’  idée"), sb.Cue(2, 2500, 9000, "ok")]
    issues = sb.check(bad, 5000).issues
    assert any("chevauchement" in i for i in issues) and any("apostrophe" in i for i in issues) and any("dépasse" in i for i in issues)


def test_real_engine_srt_shape_is_supported():
    """Un SRT au format exact du moteur (30 blocs, milliseconde) : tout est conservé et cohérent avec une durée de 56,11 s."""
    lines = []
    for i in range(30):
        start = i * 1870
        lines.append(f"{i + 1}\n00:00:{start // 1000:02d},{start % 1000:03d} --> 00:00:{(start + 1800) // 1000:02d},{(start + 1800) % 1000:03d}\nPhrase n°{i}")
    cues, check = sb.build("\n\n".join(lines) + "\n\n", "fr-FR", 56_110)
    assert len(cues) == 30 and check.ok and check.last_end_ms <= 56_110
