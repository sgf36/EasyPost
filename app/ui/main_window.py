"""Application shell: first-run gate, nav sidebar, mode banner, view stack."""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.config import APP_NAME
from app.core.client import client_manager
from app.core.settings import load_settings
from app.core.webhook_manager import webhook_manager
from app.i18n import tr
from app.ui.views.address_book_view import AddressBookView
from app.ui.views.batch_view import BatchView
from app.ui.views.claims_view import ClaimsView
from app.ui.views.create_shipment_view import CreateShipmentView
from app.ui.views.dashboard_view import DashboardView
from app.ui.views.history_view import HistoryView
from app.ui.views.hts_lookup_view import HtsLookupView
from app.ui.views.insurance_view import InsuranceView
from app.ui.views.pickups_view import PickupsView
from app.ui.views.reports_view import ReportsView
from app.ui.views.settings_view import SettingsView
from app.ui.views.setup_wizard import SetupWizard
from app.ui.views.tracking_view import TrackingView
from app.ui.widgets.async_worker import run_async
from app.ui.widgets.donation_banner import DonationBanner
from app.ui.widgets.mode_banner import ModeBanner


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 720)

        self._root_stack = QStackedWidget()
        self.setCentralWidget(self._root_stack)
        self._pending_webhook_task = None

        self._setup_wizard = SetupWizard()
        self._setup_wizard.setup_complete.connect(self._show_app_shell)
        self._root_stack.addWidget(self._setup_wizard)

        self._app_shell = self._build_app_shell()
        self._root_stack.addWidget(self._app_shell)

        client_manager.reload()
        if client_manager.credentials.active_key():
            self._root_stack.setCurrentWidget(self._app_shell)
            self._maybe_resume_webhook()
        else:
            self._root_stack.setCurrentWidget(self._setup_wizard)

    def _build_app_shell(self) -> QWidget:
        shell = QWidget()
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._mode_banner = ModeBanner()
        outer.addWidget(self._mode_banner)

        self._donation_banner = DonationBanner()
        outer.addWidget(self._donation_banner)

        body = QWidget()
        body_layout = QHBoxLayout(body)

        self._nav = QListWidget()
        self._nav.setFixedWidth(180)
        self._nav.addItems(
            [
                tr("main_window.nav_dashboard"),
                tr("main_window.nav_address_book"),
                tr("main_window.nav_create_shipment"),
                tr("main_window.nav_tracking"),
                tr("main_window.nav_history"),
                tr("main_window.nav_insurance"),
                tr("main_window.nav_pickups"),
                tr("main_window.nav_claims"),
                tr("main_window.nav_batch_shipments"),
                tr("main_window.nav_reports"),
                tr("main_window.nav_hts_lookup"),
                tr("main_window.nav_settings"),
            ]
        )
        self._nav.currentRowChanged.connect(self._on_nav_changed)

        self._view_stack = QStackedWidget()
        self._dashboard_view = DashboardView()
        self._address_book_view = AddressBookView()
        self._create_shipment_view = CreateShipmentView()
        self._tracking_view = TrackingView()
        self._history_view = HistoryView()
        self._insurance_view = InsuranceView()
        self._pickups_view = PickupsView()
        self._claims_view = ClaimsView()
        self._batch_view = BatchView()
        self._reports_view = ReportsView()
        self._hts_lookup_view = HtsLookupView()
        self._settings_view = SettingsView()
        self._view_stack.addWidget(self._dashboard_view)
        self._view_stack.addWidget(self._address_book_view)
        self._view_stack.addWidget(self._create_shipment_view)
        self._view_stack.addWidget(self._tracking_view)
        self._view_stack.addWidget(self._history_view)
        self._view_stack.addWidget(self._insurance_view)
        self._view_stack.addWidget(self._pickups_view)
        self._view_stack.addWidget(self._claims_view)
        self._view_stack.addWidget(self._batch_view)
        self._view_stack.addWidget(self._reports_view)
        self._view_stack.addWidget(self._hts_lookup_view)
        self._view_stack.addWidget(self._settings_view)

        body_layout.addWidget(self._nav)
        body_layout.addWidget(self._view_stack, stretch=1)
        outer.addWidget(body, stretch=1)

        self._nav.setCurrentRow(0)
        return shell

    def _on_nav_changed(self, index: int) -> None:
        self._view_stack.setCurrentIndex(index)
        if index == 1:  # Address Book
            self._address_book_view.refresh_table()
        elif index == 2:  # Create Shipment
            self._create_shipment_view.refresh_address_choices()
        elif index == 3:  # Tracking
            self._tracking_view.refresh_table()
        elif index == 4:  # History
            self._history_view.refresh_table()
        elif index == 6:  # Pickups
            self._pickups_view.refresh_choices()
            self._pickups_view.refresh_scheduled()
        elif index == 7:  # Claims
            self._claims_view.refresh_table()
        elif index == 8:  # Batch Shipments
            self._batch_view.refresh_address_choices()
        elif index == 9:  # Reports
            self._reports_view.refresh()
        elif index == 11:  # Settings
            self._settings_view.refresh()

    def _show_app_shell(self) -> None:
        client_manager.reload()
        self._mode_banner.refresh()
        self._root_stack.setCurrentWidget(self._app_shell)
        self._maybe_resume_webhook()

    def _maybe_resume_webhook(self) -> None:
        """Re-starts the webhook push-update tunnel on launch if it was
        left enabled last session (see app/core/webhook_manager.py) —
        off by default, opt-in only."""
        if load_settings().webhook_enabled:
            self._pending_webhook_task = run_async(webhook_manager.start, self)
