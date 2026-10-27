from sensai.core.history import History, Summary


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


# --- what a long session needs (M2) ---


def test_drop_takes_the_oldest_exchanges_and_the_notes_before_them() -> None:
    history = History()
    history.note("Profil actif : ADV")
    history.commit("Q1", "R1", "t-1")
    history.note("Profil actif : Admin")
    history.commit("Q2", "R2", "t-2")

    preview = history.messages_without(1)
    history.drop(1)

    assert (
        history.messages
        == preview
        == [
            {"role": "system", "content": "Profil actif : Admin"},  # after Q1: still true
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "R2"},
        ]
    )


def test_a_summary_comes_first_and_replaces_the_previous_one() -> None:
    history = History()
    history.commit("Q1", "R1")
    history.commit("Q2", "R2")

    history.drop(1, Summary("Résumé 1", {}))
    history.drop(0, Summary("Résumé 2", {}))
    history.drop(1)  # without a new summary, the previous one stays

    assert history.messages == [{"role": "system", "content": "Résumé 2"}]
    assert history.oldest(keep=0) == []


def test_key_figures_follow_their_exchange() -> None:
    history = History()
    history.commit("Q1", "R1", "t-1")
    history.commit("Q2", "R2", "t-2")

    history.add_key_figures("t-1", [{"label": "Marge", "value": 1.0}])
    history.add_key_figures("t-0", [{"label": "Perdu", "value": 2.0}])  # already gone

    assert [exchange.key_figures for exchange in history.exchanges] == [
        [{"label": "Marge", "value": 1.0}],
        [],
    ]
    assert [exchange.question for exchange in history.oldest(keep=1)] == ["Q1"]


def test_clear_also_empties_the_summary() -> None:
    history = History()
    history.commit("Q1", "R1")
    history.drop(1, Summary("Résumé", {}))

    history.clear()

    assert history.summary is None and len(history) == 0
