"""One carrier picker, used everywhere a carrier is chosen.

Before this existed the app named a carrier four different ways: a catalogue
dropdown on the Batch page, a free-text box on Tracking, another free-text box
on Insurance, and nothing at all on Create Shipment, where the carrier only
ever arrived attached to a rate. Two of those four accepted a typo silently and
turned it into an API error the user could not act on, and the one that did it
properly did it privately, inside app/ui/widgets/service_picker.py.

So this is the picker, and app/services/carriers.py is the matching rule it
filters with. Anything that offers a carrier goes through here; anything that
narrows a list to a carrier goes through ``carriers.for_carrier``.

Two behaviours are worth stating because they look like details and are not:

* **The list is supplied, not assumed.** ``set_carriers`` takes the codes to
  offer. A picker above a rates table is populated from the rates themselves
  (``carriers.carriers_present_in``), so it can never offer a carrier that
  would filter the view down to nothing; a picker with no view beneath it is
  populated from the whole catalogue instead.
* **Free-text mode really does mean free text.** On Tracking and Insurance the
  carrier is not a menu of what the app knows, it is a value EasyPost has to
  accept — and the catalogue is a cache that is empty on a first run offline.
  A picker that could only offer cached names would, on that run, offer none,
  and a user who knows perfectly well what their carrier is called could not
  say so. So the box stays typeable and what is typed is what is sent.
"""

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QCompleter, QWidget

from app.i18n import tr
from app.services.carriers import (
    all_carrier_codes,
    carrier_display_name,
    carrier_matches,
    list_service_levels,
)
from app.ui.widgets.async_worker import run_async

# A QComboBox defaults to AdjustToContentsOnFirstShow, and a carrier combo is
# always empty when first shown — the catalogue arrives from an async load
# afterwards. So it sizes itself to nothing and stays there: the Batch page
# published "DHL Expre" and "ExpressWorldw" on store screenshots in all seven
# languages.
#
# The floor is measured in characters, not pixels. A pixel minimum does not
# know the font, and a value generous enough for English is a value that makes
# the whole Batch page overflow its window in German — which it did, at 220px,
# pushing "Datei wählen…" off the right edge. Eighteen characters covers
# "ExpressWorldwide" and every carrier name in the catalogue.
COMBO_MIN_CHARS = 18


def widen(combo: QComboBox) -> None:
    """Give a combo a character-measured minimum width. Exported because the
    service combo next door needs the identical treatment for the identical
    reason, and two copies of this constant is how the first one went stale."""
    combo.setMinimumContentsLength(COMBO_MIN_CHARS)
    combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )


class CarrierCombo(QComboBox):
    """A carrier picker holding EasyPost carrier codes and showing their names.

    Emits :attr:`carrier_changed` with the selected code — "" for the "all
    carriers" entry, or for an empty free-text box.
    """

    carrier_changed = Signal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        include_all: bool = False,
        free_text: bool = False,
    ) -> None:
        super().__init__(parent)
        self._include_all = include_all
        self._free_text = free_text
        self._catalogue_task = None
        widen(self)

        if free_text:
            self.setEditable(True)
            self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            completer = self.completer()
            if completer is not None:
                # Carrier names are brand names with their own capitalisation
                # ("FedEx", "ePostGlobal V2"), so a case-sensitive completer
                # matches nothing for a user who types in lower case — which is
                # how anyone types into a box that used to be free text.
                completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            self.editTextChanged.connect(lambda _: self.carrier_changed.emit(self.current_carrier()))

        self.currentIndexChanged.connect(
            lambda _: self.carrier_changed.emit(self.current_carrier())
        )

    # -- population ----------------------------------------------------------

    def set_carriers(self, codes: list[str]) -> None:
        """Replace the offered carriers, keeping the current selection if it
        survives. Repopulating is routine — the catalogue loads late, and a
        rates-backed picker is rebuilt on every rating — so silently resetting
        the user's choice each time would make the control unusable."""
        previous = self.current_carrier()
        # Silent while rebuilding, then one signal if — and only if — the
        # carrier actually changed. Clearing and refilling otherwise emits for
        # every intermediate index, and each emission redraws whatever the
        # picker filters: the rates table was drawn three times per rating.
        # The previous blocked state is restored rather than forced off, so a
        # caller that blocked signals around this call stays blocked.
        was_blocked = self.blockSignals(True)
        self.clear()
        if self._include_all:
            # Data is "" rather than None so every caller reads one type back
            # from current_carrier() and "no carrier" needs no special case.
            self.addItem(tr("carrier_combo.all_carriers"), "")
        for code in codes:
            self.addItem(carrier_display_name(code), code)
        self.set_current_carrier(previous)
        self.blockSignals(was_blocked)
        if not carrier_matches(self.current_carrier(), previous):
            self.carrier_changed.emit(self.current_carrier())

    def load_catalogue(self) -> None:
        """Offer every carrier the catalogue knows, refreshing it if need be.

        The cache is read first and drawn immediately, because it is populated
        as a side effect of any other page fetching a catalogue and is therefore
        usually warm. The live fetch only runs when it is not, so opening
        Tracking does not pull ~96 carriers' service levels every time.

        A failed refresh is silent on purpose: this control is an aid to typing
        a carrier, not the means of it, so a page whose real job is adding a
        tracking number should not open with an error about a dropdown.
        """
        cached = all_carrier_codes()
        self.set_carriers(cached)
        if cached:
            return
        self._catalogue_task = run_async(list_service_levels, self)
        self._catalogue_task.succeeded.connect(lambda _levels: self.set_carriers(all_carrier_codes()))

    def set_placeholder(self, text: str) -> None:
        """Placeholder text for the typing field. A no-op unless the combo is
        in free-text mode, which is the only mode that has one."""
        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText(text)

    # -- selection -----------------------------------------------------------

    def current_carrier(self) -> str:
        """The chosen carrier code, or "" when none is chosen.

        In free-text mode the typed text wins whenever it is not the label of
        the currently selected item. Qt leaves ``currentIndex`` pointing at the
        last match while the user types over it, so reading ``currentData``
        alone would report the previous carrier for text that no longer names
        it — sending one carrier while the box on screen reads another.
        """
        if self._free_text:
            typed = self.currentText().strip()
            index = self.currentIndex()
            if index >= 0 and typed == self.itemText(index):
                return self.itemData(index) or ""
            return typed
        return self.currentData() or ""

    def set_current_carrier(self, carrier: str) -> None:
        """Select a carrier by code, matched the way carriers are matched
        everywhere else, so a CamelCase code off a rate selects the lowercase
        catalogue entry rather than silently failing to."""
        if not carrier:
            if self._include_all:
                self.setCurrentIndex(0)
            elif self._free_text:
                self.setCurrentText("")
            return
        for index in range(self.count()):
            if carrier_matches(self.itemData(index) or "", carrier):
                self.setCurrentIndex(index)
                return
        if self._free_text:
            # A carrier the catalogue has not got (an empty cache on a first
            # run offline) is still a carrier the user may name.
            self.setCurrentText(carrier)
