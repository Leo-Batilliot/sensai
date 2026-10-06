from sensai.core.history import History


def test_starts_empty() -> None:
    history = History()
    assert len(history) == 0
    assert history.messages_for("Bonjour") == [{"role": "user", "content": "Bonjour"}]


def test_messages_for_appends_the_question_after_the_history() -> None:
    history = History()
    history.commit("Bonjour", "Bonjour !")
    assert history.messages_for("Et ensuite ?") == [
        {"role": "user", "content": "Bonjour"},
        {"role": "assistant", "content": "Bonjour !"},
        {"role": "user", "content": "Et ensuite ?"},
    ]


def test_messages_for_does_not_record_the_question() -> None:
    history = History()
    history.messages_for("Question jamais terminée")
    assert len(history) == 0


def test_messages_cannot_be_changed_from_outside() -> None:
    history = History()
    history.commit("Bonjour", "Bonjour !")
    history.messages.clear()
    history.messages_for("Suite").clear()
    assert len(history) == 2


def test_clear_empties_the_history() -> None:
    history = History()
    history.commit("Bonjour", "Bonjour !")
    history.clear()
    assert len(history) == 0
    assert history.messages_for("De quoi parlait-on ?") == [
        {"role": "user", "content": "De quoi parlait-on ?"}
    ]
