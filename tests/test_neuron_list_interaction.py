from __future__ import annotations

import unittest

from PySide6 import QtCore, QtTest, QtWidgets

from fmost_brain_viewer import NeuronListWidget, color_icon


class NeuronListInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.neuron_list = NeuronListWidget()
        self.item = QtWidgets.QListWidgetItem(color_icon("#44cc66"), "Neuron 1")
        self.item.setData(QtCore.Qt.ItemDataRole.UserRole, "dataset::neuron-1")
        self.item.setFlags(
            self.item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable
        )
        self.item.setCheckState(QtCore.Qt.CheckState.Unchecked)
        self.neuron_list.addItem(self.item)
        self.neuron_list.resize(360, 100)
        self.neuron_list.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.neuron_list.close()

    def test_only_color_square_double_click_requests_color_dialog(self) -> None:
        requested = []
        self.neuron_list.colorIconDoubleClicked.connect(requested.append)
        icon_center = self.neuron_list._decoration_rect(self.item).center()
        QtTest.QTest.mouseDClick(
            self.neuron_list.viewport(),
            QtCore.Qt.MouseButton.LeftButton,
            pos=icon_center,
        )
        self.assertEqual(requested, [self.item])

        requested.clear()
        option = QtWidgets.QStyleOptionViewItem()
        option.initFrom(self.neuron_list.viewport())
        option.rect = self.neuron_list.visualItemRect(self.item)
        option.features = QtWidgets.QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        option.checkState = self.item.checkState()
        check_rect = self.neuron_list.style().subElementRect(
            QtWidgets.QStyle.SubElement.SE_ItemViewItemCheckIndicator,
            option,
            self.neuron_list.viewport(),
        )
        QtTest.QTest.mouseClick(
            self.neuron_list.viewport(),
            QtCore.Qt.MouseButton.LeftButton,
            pos=check_rect.center(),
        )
        QtTest.QTest.mouseDClick(
            self.neuron_list.viewport(),
            QtCore.Qt.MouseButton.LeftButton,
            pos=check_rect.center(),
        )
        self.assertEqual(requested, [])
        self.assertEqual(self.item.checkState(), QtCore.Qt.CheckState.Checked)


if __name__ == "__main__":
    unittest.main()
