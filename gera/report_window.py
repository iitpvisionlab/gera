from __future__ import annotations
from typing import Callable, TYPE_CHECKING
from PyQt5 import QtCore, QtSql, QtGui, QtWidgets
from .sql_text_edit import SqlTextEdit

Qt = QtCore.Qt


class GeraHeaderView(QtWidgets.QHeaderView):
    on_copy = QtCore.pyqtSignal(list)

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Horizontal, None)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.setSectionsMovable(True)
        self.setSectionsClickable(True)
        self._last_clicked_column = None
        self.addAction(
            QtWidgets.QAction(
                "&Copy whole column", self, triggered=self._on_copy
            )
        )

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent | None) -> None:
        if event is not None:
            pos = event.pos()
            self._last_clicked_column = self.logicalIndexAt(pos)
            menu = QtWidgets.QMenu(self)
            menu.addActions(self.actions())
            menu.exec_(event.globalPos())

    def _on_copy(self):
        model = self.selectionModel()
        assert model is not None
        col_idxs = [idx.column() for idx in model.selectedIndexes()]
        if not col_idxs and self._last_clicked_column is not None:
            col_idxs = [self._last_clicked_column]
        self.on_copy.emit(col_idxs)


class CustomTableView(QtWidgets.QTableView):
    def __init__(self, sql_text_edit: SqlTextEdit):
        super().__init__(
            sortingEnabled=True,
            selectionBehavior=QtWidgets.QTableView.SelectRows
            | QtWidgets.QTableView.SelectColumns,
            selectionMode=QtWidgets.QTableView.SelectionMode.ExtendedSelection,
            alternatingRowColors=True,
            contextMenuPolicy=Qt.ActionsContextMenu,
            editTriggers=QtWidgets.QTableView.EditKeyPressed,
        )
        header = GeraHeaderView()
        header.on_copy.connect(self._copy_columns)
        self.setHorizontalHeader(header)
        self._sql_text_edit = sql_text_edit

    def _copy_columns(self, col_idxs: list[int]) -> None:
        model = self.model()
        data = []
        data.append(
            [model.headerData(col_idx, Qt.Horizontal) for col_idx in col_idxs]
        )
        for row in range(model.rowCount()):
            data.append(
                [str(model.index(row, col_idx).data()) for col_idx in col_idxs]
            )
        text = "\n".join("\t".join(row) for row in data)
        QtWidgets.QApplication.clipboard().setText(text)

    def selectionChanged(
        self,
        selected: QtCore.QItemSelection,
        deselected: QtCore.QItemSelection,
    ) -> None:
        tips: set[str] = set()
        for index in selected.indexes():
            val = index.data()
            try:
                if len(val) > 1:
                    tips.add(val)
            except TypeError:  # int
                pass
        self._sql_text_edit.setTips(tips)
        super().selectionChanged(selected, deselected)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.matches(QtGui.QKeySequence.StandardKey.Copy):
            self.copy_selection(self.selectedIndexes())
        else:
            super().keyPressEvent(event)

    @staticmethod
    def copy_selection(indexes: list[QtCore.QModelIndex]) -> None:
        if not indexes:
            return
        indexes.sort(key=lambda idx: (idx.row(), idx.column()))
        rows: list[str] = []
        current_row = indexes[0].row()
        row_data: list[str] = []

        for idx in indexes:
            if idx.row() != current_row:
                rows.append("\t".join(row_data))
                row_data = []
                current_row = idx.row()
            row_data.append(str(idx.data(Qt.DisplayRole)))

        rows.append("\t".join(row_data))
        QtWidgets.QApplication.clipboard().setText("\n".join(rows))

    if TYPE_CHECKING:

        def model(self) -> QtSql.QSqlTableModel: ...


class StatisticsWidget(QtWidgets.QTableWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setSelectionMode(
            QtWidgets.QTableView.SelectionMode.ExtendedSelection
        )
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Name", "Value"])

    def set_statistics(self, data: dict[str, str]) -> None:
        self.setRowCount(len(data))
        for row, (name, value) in enumerate(data.items()):
            name_item = QtWidgets.QTableWidgetItem(name)
            value_item = QtWidgets.QTableWidgetItem()
            value_item.setData(Qt.DisplayRole, value)
            self.setItem(row, 0, name_item)
            self.setItem(row, 1, value_item)
        self.resizeColumnsToContents()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.matches(QtGui.QKeySequence.StandardKey.Copy):
            CustomTableView.copy_selection(self.selectedIndexes())
        else:
            super().keyPressEvent(event)


class ReportWindow(QtWidgets.QSplitter):
    def __init__(
        self,
        title: str,
        report_to_db: Callable[[QtSql.QSqlDatabase], list[str]],
        view_ready: Callable[[CustomTableView], None],
        db_statistics: Callable[[QtSql.QSqlDatabase, str], dict[str, str]],
    ):
        """
        :param view_ready: useful callback to let schema attach context menu
        """
        super().__init__(Qt.Vertical, windowTitle=title)
        self.setMinimumSize(1, 1)
        db_id = "db-" + str(id(self))
        self._db = self._create_db(db_id)
        self._db_statistics = db_statistics
        keys = report_to_db(self._db)
        (
            where_widget,
            self._sql_text_edit,
            apply_button,
            self._error_label,
        ) = self._create_where_widget()
        self._sql_table_view = self._create_sql_table(self._db)
        view_ready(self._sql_table_view)

        self._statistics_table = StatisticsWidget()
        self._splitter_h = splitter = QtWidgets.QSplitter()
        splitter.addWidget(self._sql_table_view)
        splitter.addWidget(self._statistics_table)
        self.addWidget(splitter)
        self.addWidget(where_widget)
        self._sql_text_edit.setColumnNames(keys)
        self._sql_text_edit.apply.connect(self._apply)
        apply_button.clicked.connect(self._apply)
        self.setSizes([1000, 1])
        self._sql_table_view.resizeColumnsToContents()
        if self._sql_table_view.model().rowCount():
            self._sql_table_view.selectRow(0)
        splitter.setSizes([10000, 0])
        self._update_stats("")

    def _apply(self):
        sql = self._sql_text_edit.toPlainText()
        sql_model = self._sql_table_view.model()
        sql_model.setFilter(sql)
        self._update_stats(sql)
        err = ""
        if sql_model.lastError().isValid():
            err = "SQL error: " + sql_model.lastError().databaseText()
            sql_model.select()
        self._error_label.setText(err)

    def _update_stats(self, filter: str) -> None:
        stats = self._db_statistics(self._db, filter)
        if stats:
            self._statistics_table.set_statistics(stats)
            self._statistics_table.setVisible(True)
            self._splitter_h.setSizes([700, 100])
        else:
            self._statistics_table.setVisible(False)

    @staticmethod
    def _create_db(connection_name: str):
        db = QtSql.QSqlDatabase.addDatabase("QSQLITE", connection_name)
        db.setDatabaseName(":memory:")
        if not db.open():
            raise Exception("Can't open sqlite database in memory")
        return db

    def _create_sql_table(self, db: QtSql.QSqlDatabase):
        """
        Create table view
        """
        table_view = CustomTableView(self._sql_text_edit)
        sql_model = QtSql.QSqlTableModel(self, db)
        sql_model.setTable("report")
        sql_model.select()
        table_view.setModel(sql_model)
        return table_view

    @staticmethod
    def _create_where_widget():
        SP = QtWidgets.QSizePolicy
        sql_text_edit = SqlTextEdit()
        action_layout = QtWidgets.QHBoxLayout()
        error_label = QtWidgets.QLabel(styleSheet="QLabel{color:darkRed;}")
        error_label.setSizePolicy(SP.Expanding, SP.Preferred)
        apply_button = QtWidgets.QPushButton(
            "Apply", toolTip="Ctrl+Enter / Alt+Enter"
        )
        action_layout.addWidget(error_label)
        action_layout.addWidget(apply_button)

        where_widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(where_widget)
        layout.addWidget(QtWidgets.QLabel("Where:"))
        layout.addWidget(sql_text_edit)
        layout.addLayout(action_layout)

        return where_widget, sql_text_edit, apply_button, error_label
