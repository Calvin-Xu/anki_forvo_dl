import os
import traceback
from datetime import datetime
from typing import List

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QWaitCondition, QMutex, pyqtSlot
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QProgressBar, QHBoxLayout, QScrollArea, QWidget, \
    QCheckBox
from anki.cards import Card
from aqt import AnkiQt
from aqt.utils import showInfo, askUser

from .Config import Config, ConfigObject
from .Exceptions import FieldNotFoundException, DownloadCancelledException, NoResultsException
from .FailedDownloadsDialog import FailedDownloadsDialog
from .FieldSelector import FieldSelector
from .Forvo import Pronunciation, Forvo
from .LanguageSelector import LanguageSelector
from .Util import FailedDownload

import hashlib
def md5(fname):
    hash_md5 = hashlib.md5()
    with open(fname, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

class BulkAdd(QDialog):
    """Dialog that opens when clicking on 'Bulk Add Forvo Audio to X cards' in the context menu in the browser"""
    def __init__(self, parent, unique_cards: List[Card], mw, config: Config):
        super().__init__(parent)
        self.config: Config = config
        self.unique_cards = unique_cards
        self.setFixedWidth(500)
        self.setFixedHeight(350)
        self.selected_pronunciation: Pronunciation = None
        self.layout = QVBoxLayout()
        self.hlayout = QHBoxLayout()
        self.hlayout.setContentsMargins(10, 10, 10, 10)

        self.hlayout.addLayout(self.layout)
        self.setLayout(self.hlayout)
        self.description = "<h1>anki-forvo-dl</h1><p>anki-forvo-dl will download audio files for the selected cards based on the selected search field and put the audio in the selected audio field.</p><p>You can change these fields by going to the add-on's directory > user_files > config.json and changing the field names there.</p>"
        self.description += "<p>Forvo offers their service for free, so please be kind and <b>don't use the bulk-add feature regularly to avoid that Forvo's servers get nuked</b>. %s cards mean %s requests to their servers. There is a delay of a second between the downloads to protect them. Try to download the audio files as you create your cards, using the blue Forvo button in the editor.</p>" % (str(len(self.unique_cards)), str(len(self.unique_cards) * 2))
        self.description_label = QLabel(text=self.description)
        self.description_label.setMinimumWidth(450)
        self.description_label.setStyleSheet("margin: 0; padding: 0;")
        self.description_label.setWordWrap(True)
        self.description_label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.description_label)

        # self.skipCheckbox = QCheckBox("Skip download for cards that already have\n content in the audio field")
        # self.skipCheckbox.setChecked(True)  # TODO read from Config
        # self.layout.addWidget(self.skipCheckbox)

        self.btn = QPushButton("Start Downloads")
        self.btn.clicked.connect(self.start_downloads_wrapper)
        self.layout.addWidget(self.btn)

        self.th = Thread(self.unique_cards, mw, config)  # Initialize Thread

        def change_skip_existing_option(skip_existing: bool, config_object):
            config_object.value = skip_existing
            self.config.set_config_object(config_object)


        skip_existing_co = self.config.get_config_object("skipExistingBulkAdd")
        self.skip_existing_checkbox = QCheckBox(skip_existing_co.description)
        self.skip_existing_checkbox.setChecked(skip_existing_co.value)
        self.skip_existing_checkbox.stateChanged.connect(lambda state: change_skip_existing_option(state == Qt.Checked, skip_existing_co))
        self.layout.addWidget(self.skip_existing_checkbox)

        self.btn_box = QHBoxLayout()

        self.pause_button = QPushButton("Pause")
        self.pause_button.setVisible(False)
        self.pause_button.clicked.connect(self.slot_pause_button)
        self.btn_box.addWidget(self.pause_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.handle_cancel_click)
        self.btn_box.addWidget(self.cancel_button)

        self.toggle_log_button = QPushButton("Toggle Log")
        self.toggle_log_button.setVisible(False)
        self.toggle_log_button.clicked.connect(self.handle_toggle_log_click)
        self.btn_box.addWidget(self.toggle_log_button)

        self.layout.addLayout(self.btn_box)

        self.progress = QProgressBar()
        self.progress.setMaximum(len(self.unique_cards))
        self.progress.setMinimum(0)
        self.progress.setVisible(False)
        self.layout.addWidget(self.progress)

        # connect thread's signals to handler functions
        self.th.change_value.connect(self.progress.setValue)
        self.th.log.connect(self.add_log_msg)
        self.th.finished.connect(self.review_downloads)

        self.parent = parent
        self.mw: AnkiQt = mw
        self.adjustSize()
        self.log: List[str] = []

        scroll_widget = QWidget()
        scroll_widget.setStyleSheet("background-color: #fff;")
        self.scroll_area = QScrollArea()
        self.scroll_vbox = QVBoxLayout(scroll_widget)

        self.scroll_area.setWidget(scroll_widget)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVisible(False)
        self.description_label.adjustSize()

    def add_log_msg(self, msg: str):
        self.log.append(msg)
        label = QLabel("<code>%s</code>" % msg)
        label.setStyleSheet("color: #000;")
        self.scroll_vbox.addWidget(label)

    def review_downloads(self):
        """Opens the FailedDownloadsDialog after downloads are completed"""
        if len(self.th.failed) > 0:
            dialog = FailedDownloadsDialog(self.parent, self.th.failed, self.mw, self.config, self.th.skipped_cards)
            dialog.finished.connect(lambda: self.close())  # close this window when the FailedDownloadsDialog is closed
            dialog.show()
        else:
            if self.th.is_cancelled:
                for card in self.th.cards:
                    if card not in self.th.done_cards:
                        self.th.failed.append(FailedDownload(card, DownloadCancelledException()))
                dialog = FailedDownloadsDialog(self.parent, self.th.failed, self.mw, self.config, self.th.skipped_cards)
                dialog.finished.connect(
                    lambda: self.close())  # close this window when the FailedDownloadsDialog is closed
                dialog.show()
            else:
                if self.th.skipped_cards == 0:
                    showInfo("All downloads finished successfully!")
                else:
                    showInfo("All downloads finished successfully!\n%s cards that already had something in their audio fields were skipped." % (str(self.th.skipped_cards)))

                self.close()

    @pyqtSlot()
    def slot_pause_button(self):
        """Pause button"""
        self.th.toggle_status()
        self.pause_button.setText({True: "Pause", False: "Resume"}[self.th.status])

    def handle_cancel_click(self):
        """Cancel button"""
        self.th.is_cancelled = True
        self.th._status = True  # allow thread to continue working so that it hits the if-statement that will cancel the thread

    @pyqtSlot()
    def handle_toggle_log_click(self):
        """Toggle log button"""
        self.scroll_area.setVisible(not self.scroll_area.isVisible())
        self.adjustSize()

    def select_lang(self, missing):
        """Recursive function that addresses all cards' decks that are missing a language"""
        deck_id = missing[len(missing) - 1]
        d = LanguageSelector(self.parent, self.mw.col.decks.get(deck_id)["name"])

        def handle_lang_select():
            if d.selected_lang is not None:
                self.config.set_deck_specific_config_object(
                    ConfigObject(name="language", value=d.selected_lang, deck=deck_id))
                if len(missing) > 1:
                    """If some decks are still missing, address them"""
                    missing.pop()
                    self.select_lang(missing)
                else:
                    """If all conflicts are done, start the downloads (yay)"""
                    self.start_downloads()
            else:
                """dummy didn't select a language"""
                showInfo("Cancelled download because no language was selected.")
                return

        d.finished.connect(handle_lang_select)
        d.show()

    def ensure_languages(self):
        """Ensures that the language is set for all selected decks; otherwise show dialog"""
        missing = list(set([card.did for card in self.unique_cards if
                            self.config.get_deck_specific_config_object("language", card.did) is None]))
        if len(missing) > 0:
            self.select_lang(missing)
        else:
            self.start_downloads()

    def start_downloads_wrapper(self):
        """Starts the whole procedure that involves ensuring fields and ensuring languages"""
        if len(self.unique_cards) > 350:
            if not askUser(title="Disclaimer", text="It has been reported that Forvo bans IPs that are downloading too many audios. You have selected %s cards, resulting in %s requests to the server. <b>Please consider to spread your downloads over a few days to avoid getting IP-banned by Forvo.</b>\nYou are responsible for what you download with this tool. Do you really want to continue?" % (str(len(self.unique_cards)), str(len(self.unique_cards) * 2))):
                self.close()
                return
        self.ensure_fields()

    def select_field(self, missing_ids: List[int], field_type: str):
        """Recursive function that addresses all decks that are missing the field assignment"""
        note_type = missing_ids[len(missing_ids) - 1]
        d = FieldSelector(self.parent, self.mw, note_type, field_type, self.config)

        def handle_field_select():
            if d.selected_field is not None:
                self.config.set_note_type_specific_config_object(
                    ConfigObject(name=field_type, value=d.selected_field, note_type=note_type))
                if len(missing_ids) > 1:
                    missing_ids.pop()
                    self.select_field(missing_ids, field_type)
                else:
                    if field_type == "audioField":
                        """If the program asked for audioFields (which come after the searchFields) and it's done, start the ensure_languages procedure"""
                        self.ensure_languages()
                        return
                    # POV: Asked for searchField -> now ask for audioField
                    new_missing = list(set([card.note_type()["id"] for card in self.unique_cards if
                                            self.config.get_note_type_specific_config_object("audioField",
                                                                                             card.note_type()[
                                                                                                 "id"]) is None]))

                    if len(new_missing) > 0:
                        self.select_field(new_missing, "audioField")
                    else:
                        """If none cards are missing their audioField, ensure the languages!"""
                        self.ensure_languages()
            else:
                """dummy didn't select their fields"""
                showInfo("Cancelled download because field wasn't selected.")
                return

        d.finished.connect(handle_field_select)
        d.show()

    def ensure_fields(self):
        missing = list(set([card.note_type()["id"] for card in self.unique_cards if
                            self.config.get_note_type_specific_config_object("searchField",
                                                                             card.note_type()["id"]) is None]))
        if len(missing) > 0:
            """If some cards don't have their searchFields assigned yet, beg the user to do so!"""
            self.select_field(missing, "searchField")
        else:
            """If all cards have a searchField assigned:"""

            new_missing = list(set([card.note_type()["id"] for card in self.unique_cards if
                                    self.config.get_note_type_specific_config_object("audioField",
                                                                                     card.note_type()[
                                                                                         "id"]) is None]))
            if len(new_missing) > 0:
                """Cards have the searchField assignment but are lacking the audioField one"""
                self.select_field(new_missing, "audioField")
            else:
                """everything's alright!"""
                self.start_downloads()

    def start_downloads(self):
        """FINALLY start the downloads"""
        self.skip_existing_checkbox.setVisible(False)
        self.btn.setVisible(False)                # }--- disable some controls and make others visible
        self.pause_button.setVisible(True)                  # }
        self.cancel_button.setVisible(True)                  # }
        self.toggle_log_button.setVisible(True)                  # }
        self.progress.setVisible(True)            # }
        self.adjustSize()   # readjust size of window, we just "added" some controls
        self.th.start()  # actually start the download thread

    def update_progress_bar(self):
        """Gets called by signal emitted by download thread, increases the progress bar by 1."""
        self.progress.setValue(self.progress.value() + 1)


class Thread(QThread):
    """The downloading is handled in a separate thread in order for the progress bar and the pause button to work"""
    change_value = pyqtSignal(int)
    done = pyqtSignal(int)
    log = pyqtSignal(str)
    is_cancelled = False
    done_cards = []


    def __init__(self, cards, mw, config):
        QThread.__init__(self)
        self.cond = QWaitCondition()
        self.mutex = QMutex()
        self.cnt = 0
        self._status = True
        self.cards = cards
        self.mw = mw
        self.skipped_cards = 0
        self.failed: List[FailedDownload] = []
        self.config: Config = config

    def __del__(self):
        self.wait()

    def run(self):
        from . import log_dir

        skip_existing = self.config.get_config_object("skipExistingBulkAdd").value

        card: Card
        for card in self.cards:
            """Go through all cards that are selected in the editor"""
            # self.mutex.lock()
            if self.is_cancelled:
                Forvo.cleanup()
                return
            if not self._status:  # If download is paused, wait
                self.cond.wait(self.mutex)
            try:  # use try to avoid stopping the entire thread because of a single exception
                # Get fields from config

                query_field = self.config.get_note_type_specific_config_object("searchField", card.note_type()["id"]).value
                audio_field = self.config.get_note_type_specific_config_object("audioField", card.note_type()["id"]).value

                if query_field not in card.note():
                    raise FieldNotFoundException(query_field)

                if audio_field not in card.note():
                    raise FieldNotFoundException(audio_field)

                query = card.note()[query_field]  # Get query string from card's note using field name
                language = self.config.get_deck_specific_config_object("language", card.did).value

                self.log.emit("[Next Card] Query: %s; Language: %s" % (query, language))

                if skip_existing and len(card.note()[audio_field]) > 0:
                    """Skip cards that already have something in the audio field if the setting is turned on"""
                    self.skipped_cards += 1
                    continue


                # Get language from config for the card's deck

                # Get the results
                results = Forvo(query, language, self.mw, self.config)
                # if results is not None:
                try:
                    if language == "ja":
                        self.log.emit("Trying to download from JapanesePod101")
                        import urllib.request
                        import urllib.parse
                        import json
                        japanesePod_url = "http://assets.languagepod101.com/dictionary/japanese/audiomp3.php?"
                        jisho_search_url = "https://jisho.org/api/v1/search/words?keyword=";
                        req_jisho = urllib.request.Request(jisho_search_url + urllib.parse.quote(query))
                        res_jisho = urllib.request.urlopen(req_jisho)
                        kana = json.load(res_jisho)["data"][0]["japanese"][0]["reading"]
                        req_japanesePod101 = urllib.request.Request(japanesePod_url + "kanji=" + urllib.parse.quote(query) + "&kana=" + urllib.parse.quote(kana))
                        from . import temp_dir
                        dl_path = os.path.join(temp_dir, "pronunciation_" + "ja" + "_" + query + ".mp3")
                        try:
                            res: HTTPResponse = urllib.request.urlopen(req_japanesePod101)
                            with open(dl_path, "wb") as f:
                                f.write(res.read())
                                res.close()
                            if md5(dl_path) == "7e2c2f954ef6051373ba916f000168dc":
                                raise NoResultsException
                            media_name = self.mw.col.media.add_file(dl_path)
                            if self.config.get_config_object("appendAudio").value:
                                card.note()[audio_field] += "[sound:%s]" % media_name  # set audio field content to the respective sound
                                self.log.emit("Appended sound string to field content")
                            else:
                                card.note()[audio_field] = "[sound:%s]" % media_name  # set audio field content to the respective sound
                                self.log.emit("Placed sound string in field")
                            card.note().flush()  # flush the toilet
                            self.log.emit("Saved note")
                        except Exception as e:
                            raise NoResultsException
                    else:
                        raise NoResultsException
                except Exception as e:
                    results = results.load_search_query().get_pronunciations().pronunciations
                    results.sort(key=lambda result: result.votes)  # sort by votes

                    top: Pronunciation = results[len(results) - 1]  # get most upvoted pronunciation
                    self.log.emit("Selected pronunciation by %s with %s votes" % (top.user, str(top.votes)))
                    top.download_pronunciation()  # download that
                    self.log.emit("Downloaded pronunciation")
                    if self.config.get_config_object("appendAudio").value:
                        card.note()[audio_field] += "[sound:%s]" % top.audio  # set audio field content to the respective sound
                        self.log.emit("Appended sound string to field content")
                    else:
                        card.note()[audio_field] = "[sound:%s]" % top.audio  # set audio field content to the respective sound
                        self.log.emit("Placed sound string in field")

                    card.note().flush()  # flush the toilet
                    self.log.emit("Saved note")
            except Exception as e:
                # Save all raised exceptions in a list to retrieve them later in the FailedDownloadsDialog
                self.failed.append(FailedDownload(reason=e, card=card))
                self.log.emit("[Error] Card with 1. Field %s failed due to Exception: %s" % (card.note().fields[0], str(e)))
                with open(os.path.join(log_dir, "bulk_error_log-" + datetime.now().strftime('%Y-%m-%dT%H') + ".log"), "a", encoding="utf8") as f:
                    f.write("\n".join(traceback.format_exception(None, e, e.__traceback__)) + "\n------------------\n")


            self.done_cards.append(card)
            self.cnt += 1  # Increase count for progress bar
            self.change_value.emit(self.cnt)  # emit signal to update progress bar
            self.msleep(1000)  # sleep to give progress bar time to update

            # self.mutex.unlock()

        Forvo.cleanup()  # cleanup files in temp directory (None is passed as the self parameter here)

    def toggle_status(self):  # toggle pause state
        self._status = not self._status
        if self._status:
            self.cond.wakeAll()

    @property
    def status(self):
        return self._status
