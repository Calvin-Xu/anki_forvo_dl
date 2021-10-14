import pathlib
from typing import List, Tuple

import anki
from anki.hooks import addHook
from aqt import mw, gui_hooks
from aqt.browser import Browser
from aqt.editor import Editor
from aqt.qt import *
from aqt.utils import showInfo, showWarning

from .About import About
from .AddSingle import AddSingle
from .BulkAdd import BulkAdd
from .Config import Config, ConfigObject
from .ConfigManager import ConfigManager
from .Exceptions import NoResultsException, FieldNotFoundException
from .FieldSelector import FieldSelector
from .Forvo import Forvo, Pronunciation
from .LanguageSelector import LanguageSelector
from .Util import get_field_id

"""Paths to directories get determined based on __file__"""
asset_dir = os.path.join(pathlib.Path(__file__).parent.absolute(), "assets")
temp_dir = os.path.join(pathlib.Path(__file__).parent.absolute(), "temp")
user_files_dir = os.path.join(pathlib.Path(__file__).parent.absolute(), "user_files")
log_dir = os.path.join(pathlib.Path(__file__).parent.absolute(), "user_files", "logs")

debug_mode = os.path.isfile(os.path.join(user_files_dir, ".debug"))

"""Ensure directories (create if not existing)"""
for path in [temp_dir, user_files_dir, log_dir]:
    if not os.path.exists(path):
        os.makedirs(path)

config = Config(os.path.join(user_files_dir, "config.json"),
                os.path.join(asset_dir, "config.template.json")).load_config().load_template().ensure_options()


def _handle_field_select(d, note_type_id, field_type, editor):
    if d.selected_field is not None:
        config.set_note_type_specific_config_object(
            ConfigObject(name=field_type, value=d.selected_field, note_type=note_type_id))
        on_editor_btn_click(editor, False)
    else:
        showInfo("Cancelled download because fields weren't selected.")


def select_field(editor: Editor, note_type_id, field_type):
    d = FieldSelector(editor.parentWindow, editor.mw, note_type_id, field_type, config)
    d.finished.connect(lambda: _handle_field_select(d, note_type_id, field_type, editor))
    d.show()


def on_editor_btn_click(editor: Editor, choose_automatically: Union[None, bool] = None):
    if choose_automatically is None:
        modifiers = QApplication.keyboardModifiers()
        if modifiers == Qt.ShiftModifier:
            """Choose top pronunciation automatically when shift key is held down"""
            choose_automatically = True

    deck_id = editor.card.did if editor.card is not None else editor.parentWindow.deckChooser.selectedId()
    note_type_id = editor.card.note().mid if editor.card is not None else editor.mw.col.models.current()["id"]
    search_field = config.get_note_type_specific_config_object("searchField", note_type_id)
    if search_field is None or search_field.value not in editor.note.keys():
        select_field(editor, note_type_id, "searchField")
        return

    audio_field = config.get_note_type_specific_config_object("audioField", note_type_id)
    if audio_field is None or audio_field.value not in editor.note.keys():
        select_field(editor, note_type_id, "audioField")
        return

    search_field = search_field.value
    audio_field = audio_field.value

    if editor.note is None:
        showInfo("Please enter a search term in the field '" + search_field + "'.")
        return

    if editor.note is not None and search_field in editor.note.keys() and len(editor.note[search_field]) != 0:
        """If available, use the content of the defined search field as the query"""
        query = editor.note[search_field]
    elif editor.note is not None and editor.currentField is not None and editor.note.fields[
        editor.currentField] is not None and len(editor.note.fields[editor.currentField]) != 0:
        """If the search field is empty, use the content of the currently selected field"""
        query = editor.note.fields[editor.currentField]
    else:
        showInfo("Please enter a search term in the field '" + search_field + "'.")
        return

    if deck_id is not None:
        def proceed(language):
            try:
                forvo = Forvo(query, language, editor.mw, config).load_search_query()
                if forvo is not None:
                    results = forvo.get_pronunciations().pronunciations
                else:
                    raise Exception()
                    return
            except NoResultsException:
                showInfo("No results found! :(")
                return

            if choose_automatically:
                def add_automatically():
                    """If shift key is held down"""
                    results.sort(key=lambda result: result.votes)  # sort by votes
                    top: Pronunciation = results[len(results) - 1]  # get most upvoted pronunciation
                    top.download_pronunciation()  # download that
                    try:
                        if config.get_config_object("appendAudio").value:
                            editor.note.fields[
                                get_field_id(audio_field, editor.note)] += "[sound:%s]" % top.audio
                        else:
                            editor.note.fields[
                                get_field_id(audio_field, editor.note)] = "[sound:%s]" % top.audio
                    except FieldNotFoundException:
                        showWarning(
                            "Couldn't find field '%s' for adding the audio string. Please create a field with this name or change it in the config for the note type id %s" % (
                                audio_field, str(note_type_id)))

                    if config.get_config_object("playAudioAfterSingleAddAutomaticSelection").value:  # play audio if desired
                        anki.sound.play(top.audio)

                    def flush_field():
                        if not editor.addMode:  # save
                            editor.note.flush()
                        editor.currentField = get_field_id(audio_field, editor.note)
                        editor.loadNote(focusTo=get_field_id(audio_field, editor.note))

                    editor.saveNow(flush_field, keepFocus=True)

                editor.saveNow(add_automatically, keepFocus=False)
            else:
                dialog = AddSingle(editor.parentWindow, pronunciations=results)

                def handle_close():
                    Forvo.cleanup()
                    if dialog.selected_pronunciation is not None:
                        try:
                            if config.get_config_object("appendAudio").value:
                                editor.note.fields[
                                    get_field_id(audio_field,
                                                 editor.note)] += "[sound:%s]" % dialog.selected_pronunciation.audio
                            else:
                                editor.note.fields[
                                    get_field_id(audio_field,
                                                 editor.note)] = "[sound:%s]" % dialog.selected_pronunciation.audio
                        except FieldNotFoundException:
                            showWarning(
                                "Couldn't find field '%s' for adding the audio string. Please create a field with this name or change it in the config for the note type id %s" % (
                                    audio_field, str(note_type_id)))
                        if not editor.addMode:
                            editor.note.flush()
                        editor.loadNote()

                dialog.finished.connect(handle_close)
                dialog.show()

        config_lang = config.get_deck_specific_config_object("language", deck_id)

        if config_lang is not None:
            proceed(config_lang.value)
        else:
            d = LanguageSelector(editor.parentWindow, mw.col.decks.get(deck_id)["name"])

            def handle_lang_select():
                if d.selected_lang is not None:
                    config.set_deck_specific_config_object(
                        ConfigObject(name="language", value=d.selected_lang, deck=deck_id))
                    proceed(d.selected_lang)
                else:
                    showInfo("Cancelled download because no language was selected.")

            d.finished.connect(handle_lang_select)
            d.show()


def on_browser_ctx_menu_click(browser: Browser, selected):
    cards = [browser.mw.col.getCard(card) for card in selected]
    unique_cards = []
    addressed_unique_cards = []
    for card in cards:
        if card.nid in addressed_unique_cards:
            continue
        unique_cards.append(card)
        addressed_unique_cards.append(card.nid)

    dialog = BulkAdd(browser.window(), unique_cards, browser.mw, config)
    dialog.show()


def add_editor_button(buttons: List[str], editor: Editor):
    editor._links["forvo_dl"] = on_editor_btn_click
    if os.path.isabs(os.path.join(asset_dir, "icon.png")):
        iconstr = editor.resourceToData(os.path.join(asset_dir, "icon.png"))
    else:
        iconstr = "/_anki/imgs/{}.png".format(os.path.join(asset_dir, "icon.png"))

    return buttons + [
        "<div title=\"Hold down shift + click to select top audio\n\nCTRL+F to open window\nCTRL+SHIFT+F to select top audio\" style=\"float: right; margin: 0 3px\"><div style=\"display: flex; width: 50px; height: 25px; justify-content: center; align-items: center; padding: 0 5px; border-radius: 5px; background-color: #0094FF; color: #ffffff; font-size: 10px\" onclick=\"pycmd('forvo_dl');return false;\"><img style=\"margin-right: 5px; margin-left: 5px; height: 20px; width: 20px\" src=\"%s\"/><b style=\"user-select: none; margin-right: 7px\">Forvo</b></div></div>" % iconstr]


def add_editor_shortcut(shortcuts: List[Tuple], editor: Editor):
    shortcuts.append(("Ctrl+F", lambda: on_editor_btn_click(editor, False)))
    shortcuts.append(("Ctrl+Shift+F", lambda: on_editor_btn_click(editor, True)))


def add_browser_context_menu_entry(browser: Browser, m: QMenu):
    selected = browser.selectedCards()

    cards = [browser.mw.col.getCard(card) for card in selected]
    unique_cards = []
    addressed_unique_cards = []
    for card in cards:
        if card.nid in addressed_unique_cards:
            continue
        unique_cards.append(card)
        addressed_unique_cards.append(card.nid)

    m.addSeparator()
    action = m.addAction(QIcon(os.path.join(asset_dir, "icon.png")),
                         "Bulk add Forvo audio to " + str(len(selected)) + " cards (%s unique cards)" % str(
                             len(unique_cards)) + "...")
    action.triggered.connect(lambda: on_browser_ctx_menu_click(browser, selected))


about = About(mw)
addHook("setupEditorButtons", add_editor_button)
gui_hooks.browser_will_show_context_menu.append(add_browser_context_menu_entry)
gui_hooks.editor_did_init_shortcuts.append(add_editor_shortcut)
