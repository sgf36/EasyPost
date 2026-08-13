"""Carrier + service selection and delivery options for a batch.

A batch is never rated by EasyPost, so unlike a single shipment there is no
rates table to pick from: the carrier and service have to be chosen up front, by
name. This widget is where that happens, backed by the live catalogue in
app/services/carriers.py so the names are real ones rather than typed guesses.

It also enforces the signature precondition. Verified against the live API: a
batch naming a "signed for" service *without*
``options.delivery_confirmation = "SIGNATURE"`` is created quite happily and
then fails at purchase with "RoyalMailV3 does not offer service
RoyalMail2ndClassSignedFor for this shipment". Since that failure is invisible
until money is meant to change hands, choosing such a service here ticks the
signature box and locks it on rather than letting the user create a batch that
cannot be bought.
"""

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.services.carriers import (
    ServiceLevel,
    carrier_display_name,
    enabled_carrier_codes,
    list_service_levels,
)
from app.services.insurance import INSURANCE_MAX_USD
from app.ui.theme import TEXT_MUTED
from app.ui.widgets.async_worker import run_async


# A QComboBox defaults to AdjustToContentsOnFirstShow, and both of these are
# empty when first shown — carriers and services arrive from an async catalogue
# load afterwards. So each sized itself to nothing and stayed there: the Batch
# page published "DHL Expre" and "ExpressWorldw" on store screenshots in all
# seven languages. Same defect as the Package combo's "Custom di" (6070f4a),
# different cause, so that fix did not cover these.
#
# The floor is measured in characters, not pixels. A pixel minimum does not
# know the font, and a value generous enough for English is a value that makes
# the whole Batch page overflow its window in German — which it did, at 220px,
# pushing "Datei wählen…" off the right edge. Eighteen characters covers
# "ExpressWorldwide" and every carrier name in the catalogue.
_COMBO_MIN_CHARS = 18


def _widen(combo: QComboBox) -> None:
    combo.setMinimumContentsLength(_COMBO_MIN_CHARS)
    combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )


@dataclass(frozen=True)
class ServiceSelection:
    """What the user chose, in the exact shapes the batch service expects."""

    carrier: str
    service: str
    delivery_confirmation: Optional[str] = None
    insurance: Optional[str] = None
    auto_track: bool = True


class ServicePicker(QGroupBox):
    """Carrier/service pickers plus the signature, insurance and tracking
    options. Emits ``changed`` whenever the selection becomes (in)complete so a
    parent view can enable or disable its own actions."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(tr("batch_shipments.service_group_title"), parent)
        self._levels: list[ServiceLevel] = []
        self._enabled_codes: Optional[set[str]] = None
        self._pending_task = None
        # (carrier, service) pairs a real rating actually returned, and the
        # price each quoted. Empty until the batch page rates a row; see
        # set_quoted_services.
        self._quoted: dict[tuple[str, str], str] = {}
        self._quoted_line: Optional[int] = None

        self._carrier_combo = QComboBox()
        _widen(self._carrier_combo)
        self._carrier_combo.currentIndexChanged.connect(self._on_carrier_changed)

        self._show_all_check = QCheckBox(tr("batch_shipments.show_all_carriers"))
        self._show_all_check.setToolTip(tr("batch_shipments.show_all_carriers_tip"))
        self._show_all_check.toggled.connect(lambda _: self._on_show_all_toggled())

        carrier_row = QHBoxLayout()
        carrier_row.setContentsMargins(0, 0, 0, 0)
        carrier_row.addWidget(self._carrier_combo, stretch=1)
        carrier_row.addWidget(self._show_all_check)
        carrier_row_widget = QWidget()
        carrier_row_widget.setLayout(carrier_row)

        # Royal Mail alone publishes 243 services, so a plain dropdown is not
        # navigable — the filter is what makes the list usable.
        self._service_filter = QLineEdit()
        self._service_filter.setPlaceholderText(tr("batch_shipments.service_filter_placeholder"))
        self._service_filter.setClearButtonEnabled(True)
        self._service_filter.textChanged.connect(lambda _: self._populate_services())

        self._service_combo = QComboBox()
        _widen(self._service_combo)
        self._service_combo.currentIndexChanged.connect(self._on_service_changed)

        service_box = QVBoxLayout()
        service_box.setContentsMargins(0, 0, 0, 0)
        service_box.addWidget(self._service_filter)
        service_box.addWidget(self._service_combo)
        service_widget = QWidget()
        service_widget.setLayout(service_box)

        self._signature_check = QCheckBox(tr("batch_shipments.signature_label"))
        self._signature_check.toggled.connect(lambda _: self.changed.emit())

        self._insurance_check = QCheckBox(tr("batch_shipments.insurance_label"))
        self._insurance_amount = QDoubleSpinBox()
        self._insurance_amount.setPrefix("$ ")
        self._insurance_amount.setDecimals(2)
        self._insurance_amount.setMaximum(INSURANCE_MAX_USD)
        self._insurance_amount.setEnabled(False)
        self._insurance_check.toggled.connect(self._insurance_amount.setEnabled)
        self._insurance_check.toggled.connect(lambda _: self.changed.emit())

        insurance_row = QHBoxLayout()
        insurance_row.setContentsMargins(0, 0, 0, 0)
        insurance_row.addWidget(self._insurance_check)
        insurance_row.addWidget(self._insurance_amount, stretch=1)
        insurance_widget = QWidget()
        insurance_widget.setLayout(insurance_row)

        self._track_check = QCheckBox(tr("batch_shipments.track_label"))
        self._track_check.setChecked(True)
        self._track_check.setToolTip(tr("batch_shipments.track_tip"))

        self._note_label = QLabel("")
        self._note_label.setWordWrap(True)
        self._note_label.setStyleSheet(f"color: {TEXT_MUTED};")

        form = QFormLayout()
        form.addRow(tr("batch_shipments.carrier_label"), carrier_row_widget)
        form.addRow(tr("batch_shipments.service_label"), service_widget)
        form.addRow("", self._signature_check)
        form.addRow(tr("batch_shipments.insurance_row_label"), insurance_widget)
        form.addRow("", self._track_check)
        form.addRow("", self._note_label)
        self.setLayout(form)

        self._set_note(tr("batch_shipments.catalogue_loading"))

    # -- catalogue -----------------------------------------------------------

    def load_catalogue(self) -> None:
        """Fetch the service catalogue off the UI thread. Falls back to the
        local cache internally, so this stays useful offline."""

        def _fetch():
            # enabled_carrier_codes() runs second on purpose: it matches account
            # labels against carrier names the fetch above has just cached.
            levels = list_service_levels()
            return levels, enabled_carrier_codes()

        self._pending_task = run_async(_fetch, self)
        self._pending_task.succeeded.connect(self._on_catalogue_loaded)
        self._pending_task.failed.connect(
            lambda exc: self._set_note(tr("batch_shipments.catalogue_failed", error=str(exc)))
        )

    def _on_catalogue_loaded(self, payload) -> None:
        self._levels, self._enabled_codes = payload
        if not self._levels:
            self._set_note(tr("batch_shipments.catalogue_empty"))
        else:
            self._set_note("")
        # With no way to tell which carriers are enabled (test mode answers the
        # accounts endpoint with a ForbiddenError), showing everything is the
        # only honest default — an empty picker would be worse than a long one.
        if self._enabled_codes is None:
            self._show_all_check.setChecked(True)
            self._show_all_check.setEnabled(False)
            self._show_all_check.setToolTip(tr("batch_shipments.show_all_forced_tip"))
        self._populate_carriers()

    def set_quoted_services(self, quotes: list[dict], line_number: Optional[int] = None) -> None:
        """Narrow the pickers to what a real rating actually returned.

        `quotes` comes from rating one representative row (see
        batches.rate_representative_row). It is a guide, not a guarantee: rows
        can differ, so a service quoted for the rated parcel may not suit every
        other one. That is why it filters rather than replaces, and why "Show
        all carriers" — which already means "stop narrowing this list" — turns
        it off, instead of introducing a second switch that means the same
        thing.
        """
        self._quoted = {
            (q["carrier"], q["service"]): (
                f"{q['rate']} {q['currency']}" if q.get("rate") and q.get("currency") else ""
            )
            for q in quotes
        }
        self._quoted_line = line_number
        self._populate_carriers()
        self._refresh_note()

    def _filtering_to_quoted(self) -> bool:
        return bool(self._quoted) and not self._show_all_check.isChecked()

    def _carrier_codes(self) -> list[str]:
        codes = {s.carrier for s in self._levels}
        if self._filtering_to_quoted():
            quoted = {c for c, _ in self._quoted}
            # A rating can name a carrier the catalogue has not cached. Keeping
            # it would leave a carrier with no selectable services, so the
            # intersection is what is shown.
            if quoted & codes:
                return sorted(quoted & codes, key=lambda c: carrier_display_name(c).casefold())
        if not self._show_all_check.isChecked() and self._enabled_codes:
            enabled = {c for c in codes if c in self._enabled_codes}
            # Never leave the picker empty: if nothing matched, the account-type
            # mapping failed rather than the user having no carriers, and hiding
            # every option would make the page unusable.
            if enabled:
                codes = enabled
        return sorted(codes, key=lambda c: carrier_display_name(c).casefold())

    def _populate_carriers(self) -> None:
        previous = self._carrier_combo.currentData()
        self._carrier_combo.blockSignals(True)
        self._carrier_combo.clear()
        for code in self._carrier_codes():
            self._carrier_combo.addItem(carrier_display_name(code), code)
        if previous:
            index = self._carrier_combo.findData(previous)
            if index >= 0:
                self._carrier_combo.setCurrentIndex(index)
        self._carrier_combo.blockSignals(False)
        self._populate_services()

    def _on_carrier_changed(self, _index: int) -> None:
        self._populate_services()

    def _services_for_current_carrier(self) -> list[ServiceLevel]:
        carrier = self._carrier_combo.currentData()
        if not carrier:
            return []
        needle = self._service_filter.text().strip().casefold()
        matches = [s for s in self._levels if s.carrier == carrier]
        if self._filtering_to_quoted():
            quoted = [s for s in matches if (s.carrier, s.name) in self._quoted]
            # Same reasoning as the carrier list: an empty picker is worse than
            # an unfiltered one, so a carrier the rating covered but the
            # catalogue names differently falls back to everything it has.
            if quoted:
                matches = quoted
        if needle:
            matches = [
                s for s in matches
                if needle in s.name.casefold() or needle in (s.human_readable or "").casefold()
            ]
        return sorted(matches, key=lambda s: s.name.casefold())

    def _populate_services(self) -> None:
        previous = self._service_combo.currentData()
        self._service_combo.blockSignals(True)
        self._service_combo.clear()
        for level in self._services_for_current_carrier():
            # The quoted price rides on the label when one is known. Choosing a
            # batch service was previously done entirely blind to cost.
            price = self._quoted.get((level.carrier, level.name), "")
            label = f"{level.display_name} — {price}" if price else level.display_name
            self._service_combo.addItem(label, level.name)
        if previous:
            index = self._service_combo.findData(previous)
            if index >= 0:
                self._service_combo.setCurrentIndex(index)
        self._service_combo.blockSignals(False)
        self._on_service_changed(self._service_combo.currentIndex())

    def _current_level(self) -> Optional[ServiceLevel]:
        name = self._service_combo.currentData()
        carrier = self._carrier_combo.currentData()
        if not name or not carrier:
            return None
        return next(
            (s for s in self._levels if s.carrier == carrier and s.name == name), None
        )

    def _on_show_all_toggled(self) -> None:
        self._populate_carriers()
        self._refresh_note()

    def _on_service_changed(self, _index: int) -> None:
        level = self._current_level()
        if level is not None and level.requires_signature:
            # Locked on rather than merely pre-ticked: without it the batch is
            # created and then refuses to be bought, which is a far worse
            # outcome than a checkbox the user cannot untick.
            self._signature_check.setChecked(True)
            self._signature_check.setEnabled(False)
        else:
            self._signature_check.setEnabled(True)
        self._refresh_note()
        self.changed.emit()

    def _refresh_note(self) -> None:
        """One line, and the signature warning outranks the filter notice.

        The signature note explains why a box is locked; the filter notice is
        informational. Showing the latter over the former would hide the reason
        a control cannot be changed.
        """
        level = self._current_level()
        if level is not None and level.requires_signature:
            self._set_note(tr("batch_shipments.signature_required_note", service=level.display_name))
        elif self._filtering_to_quoted():
            self._set_note(tr("batch_shipments.quoted_only_note", line=self._quoted_line or ""))
        else:
            self._set_note("")

    def _set_note(self, text: str) -> None:
        self._note_label.setText(text)
        self._note_label.setVisible(bool(text))

    # -- result --------------------------------------------------------------

    def is_complete(self) -> bool:
        return bool(self._carrier_combo.currentData() and self._service_combo.currentData())

    def selection(self) -> Optional[ServiceSelection]:
        if not self.is_complete():
            return None
        insurance = None
        if self._insurance_check.isChecked() and self._insurance_amount.value() > 0:
            # EasyPost takes insurance as a string amount, always in USD.
            insurance = f"{self._insurance_amount.value():.2f}"
        return ServiceSelection(
            carrier=self._carrier_combo.currentData(),
            service=self._service_combo.currentData(),
            delivery_confirmation="SIGNATURE" if self._signature_check.isChecked() else None,
            insurance=insurance,
            auto_track=self._track_check.isChecked(),
        )
