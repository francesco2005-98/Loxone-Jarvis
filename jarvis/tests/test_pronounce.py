from jarvis.audio.pronounce import pronounce


def test_english_words_keep_their_sound_in_german():
    said = pronounce("Sehr wohl, Sir. Ich starte Spotify.", "de")
    assert "[[sˈɜː]]" in said and "[[spˈɒtɪfˌaɪ]]" in said
    assert "Sir" not in said and "Spotify" not in said
    assert pronounce("Wie Sie wünschen, Ma'am.", "de").endswith("[[mˈæm]].")
    assert pronounce("Wie Sie wünschen, Mam.", "de").endswith("[[mˈæm]].")


def test_english_speech_and_german_words_stay_untouched():
    sentence = "Very good, Sir. Spotify is playing."
    assert pronounce(sentence, "en") == sentence
    assert pronounce("Das Licht im Wohnzimmer ist an.", "de") == "Das Licht im Wohnzimmer ist an."
