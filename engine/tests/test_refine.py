from huddle_engine.refine import apply_replacements, html_to_text


def test_html_to_text_keeps_bullets_and_lines():
    html = "<div>Speaker 2 is <b>Shana</b></div><ul><li>Project is Plano, not Planet</li><li>No launch date decided</li></ul><p>Thanks &amp; bye</p>"
    assert html_to_text(html) == "Speaker 2 is Shana\n- Project is Plano, not Planet\n- No launch date decided\nThanks & bye"
    assert html_to_text("") == "" and html_to_text(None) == ""
    assert html_to_text("plain text") == "plain text"


def test_apply_replacements_is_word_bounded_and_keeps_capitals():
    text = "Planet gaat live. We bespreken planet en de Planetarium-site."
    out, n = apply_replacements(text, [("planet", "Plano")])
    assert out == "Plano gaat live. We bespreken Plano en de Planetarium-site."
    assert n == 2
    # a capitalised replacement (a name) stays capitalised even where the transcript had lower case
    out2, n2 = apply_replacements("Sanne zei dat sanne akkoord is", [("Sanne", "Shana")])
    assert out2 == "Shana zei dat Shana akkoord is" and n2 == 2


def test_model_output_lists_are_accepted_as_text():
    from huddle_engine.providers.summarize import NotesOut, SpeakersOut
    from huddle_engine.refine import RefineOut
    r = RefineOut.model_validate({"speakerRenames": [], "replacements": [], "context": ["fix A", "fix B"]})
    assert r.context == "fix A\nfix B"
    n = NotesOut.model_validate({"title": ["Acme website", "sprint"], "summary": "ok"})
    assert n.title == "Acme website\nsprint"
    s = SpeakersOut.model_validate({"speakers": [{"label": "Speaker 2", "name": ["Shana"], "confidence": 0.9}]})
    assert s.speakers[0].name == "Shana"
