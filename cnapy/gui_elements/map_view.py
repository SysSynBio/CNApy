"""The PyNetAnalyzer map view"""
import math
from ast import literal_eval as make_tuple
from math import isclose
from typing import Dict, Tuple

from PySide2.QtCore import QMimeData, QRectF, Qt, Signal
from PySide2.QtGui import QColor, QDrag, QMouseEvent, QPainter, QPalette
from PySide2.QtSvg import QGraphicsSvgItem
from PySide2.QtWidgets import (QAction, QGraphicsItem, QGraphicsProxyWidget,
                               QGraphicsScene, QGraphicsSceneDragDropEvent,
                               QGraphicsSceneMouseEvent, QGraphicsView,
                               QLineEdit, QMenu, QWidget)

from cnapy.cnadata import CnaData

INCREASE_FACTOR = 1.1
DECREASE_FACTOR = 0.9


class MapView(QGraphicsView):
    """A map of reaction boxes"""

    def __init__(self, appdata: CnaData, idx):
        self.scene = QGraphicsScene()
        QGraphicsView.__init__(self, self.scene)
        palette = self.palette()
        palette.setColor(QPalette.Base, Qt.white)
        self.setPalette(palette)

        self.appdata = appdata
        self.idx = idx
        self.setAcceptDrops(True)
        self.drag = False
        self.reaction_boxes: Dict[str, ReactionBox] = {}
        self._zoom = 0
        self.drag = False

        # initial scale
        self._zoom = self.appdata.project.maps[self.idx]["zoom"]
        if self._zoom > 0:
            for i in range(1, self._zoom):
                self.scale(INCREASE_FACTOR, INCREASE_FACTOR)
        if self._zoom < 0:
            for i in range(self._zoom, -1):
                self.scale(DECREASE_FACTOR, DECREASE_FACTOR)

        # connect events to methods
        self.horizontalScrollBar().valueChanged.connect(self.on_hbar_change)
        self.verticalScrollBar().valueChanged.connect(self.on_vbar_change)

    def on_hbar_change(self, x):
        self.appdata.project.maps[self.idx]["pos"] = (
            x, self.verticalScrollBar().value())

    def on_vbar_change(self, y):
        self.appdata.project.maps[self.idx]["pos"] = (
            self.horizontalScrollBar().value(), y)

    def dragEnterEvent(self, event: QGraphicsSceneDragDropEvent):
        event.setAccepted(True)
        event.accept()
        event.acceptProposedAction()

    def dragMoveEvent(self, event: QGraphicsSceneDragDropEvent):
        event.setAccepted(True)
        point = event.pos()
        point_item = self.mapToScene(point)
        key = event.mimeData().text()
        (_, _, name) = self.appdata.project.maps[self.idx]["boxes"][key]
        self.appdata.project.maps[self.idx]["boxes"][key] = (
            point_item.x(), point_item.y(), name)
        self.update()

    def dragLeaveEvent(self, _event):
        self.update()

    def dropEvent(self, event: QGraphicsSceneDragDropEvent):
        self.drag = False
        point = event.pos()
        point_item = self.mapToScene(point)
        key = event.mimeData().text()
        (_, _, name) = self.appdata.project.maps[self.idx]["boxes"][key]
        self.appdata.project.maps[self.idx]["boxes"][key] = (
            point_item.x(), point_item.y(), name)
        self.update()

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            factor = INCREASE_FACTOR
            self._zoom += 1
        else:
            factor = DECREASE_FACTOR
            self._zoom -= 1

        self.appdata.project.maps[self.idx]["zoom"] = self._zoom
        self.scale(factor, factor)

    # def toggleDragMode(self):
    #     if self.dragMode() == QGraphicsView.ScrollHandDrag:
    #         self.setDragMode(QGraphicsView.NoDrag)
    #     elif not self._photo.pixmap().isNull():
    #         self.setDragMode(QGraphicsView.ScrollHandDrag)
    def mouseDoubleClickEvent(self, event: QMouseEvent):
        print("Mapview::double_clickEvent")
        x = self.itemAt(event.pos())
        print(x)
        if isinstance(x, QGraphicsProxyWidget):
            print("yeah")
            w = x.widget()
            print(w)
        elif isinstance(x, ReactionBox):
            print("juuh")
            self.doubleClickedReaction.emit(x.key)
            # check if reaction box is under the cursor
        super(MapView, self).mouseDoubleClickEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        print("MapView::mousePressEvent")
        self.drag = True
        super(MapView, self).mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent):
        # print("mouse-move")
        if self.drag:
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self.translate(1, 1)
        super(MapView, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        print("Mapview::mouseReleaseEvent")
        if self.drag:
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self.translate(1, 1)
        self.drag = False
        super(MapView, self).mouseReleaseEvent(event)

    def update_selected(self, string):
        print("mapview:update_selected", string)

        for id in self.reaction_boxes:
            if string.lower() in id.lower():
                self.reaction_boxes[id].item.setHidden(False)
            else:
                self.reaction_boxes[id].item.setHidden(True)

    def focus_reaction(self, reaction: str):
        print("mapview:focus_reaction", reaction)
        x = self.appdata.project.maps[self.idx]["boxes"][reaction][0]
        y = self.appdata.project.maps[self.idx]["boxes"][reaction][1]
        self.centerOn(x, y)

    def highlight_reaction(self, string):
        print("mapview:highlight", string)

        # hide other boxes
        # for id in self.reaction_boxes:
        #     self.reaction_boxes[id].item.setHidden(True)

        treffer = self.reaction_boxes[string]
        treffer.item.setHidden(False)

        treffer.set_color(Qt.magenta)

    def update(self):
        print("MapView::update", self.idx)
        self.scene.clear()
        background = QGraphicsSvgItem(
            self.appdata.project.maps[self.idx]["background"])
        background.setFlags(QGraphicsItem.ItemClipsToShape)
        background.setScale(self.appdata.project.maps[self.idx]["bg-size"])
        self.scene.addItem(background)

        for key in self.appdata.project.maps[self.idx]["boxes"]:
            box = ReactionBox(self, key)
            box.setPos(self.appdata.project.maps[self.idx]["boxes"][key]
                       [0], self.appdata.project.maps[self.idx]["boxes"][key][1])
            self.scene.addItem(box)
            self.reaction_boxes[key] = box

        self.set_values()

        # set scrollbars

        self.horizontalScrollBar().setValue(
            self.appdata.project.maps[self.idx]["pos"][0])
        self.verticalScrollBar().setValue(
            self.appdata.project.maps[self.idx]["pos"][1])

    def set_values(self):
        for key in self.appdata.project.maps[self.idx]["boxes"]:
            if key in self.appdata.project.scen_values.keys():
                self.reaction_boxes[key].set_val_and_color(
                    self.appdata.project.scen_values[key])
            elif key in self.appdata.project.comp_values.keys():
                self.reaction_boxes[key].set_val_and_color(
                    self.appdata.project.comp_values[key])

    def delete_box(self, key):
        # print("MapView::delete_box", key)
        del self.appdata.project.maps[self.idx]["boxes"][key]
        self.update()

    def emit_doubleClickedReaction(self, reaction: str):
        print("emit_doubleClickedReaction")
        self.doubleClickedReaction.emit(reaction)

    def value_changed(self, reaction: str, value: str):
        print("emit_value_changed")
        self.reactionValueChanged.emit(reaction, value)
        self.reaction_boxes[reaction].recolor()

    doubleClickedReaction = Signal(str)
    reactionValueChanged = Signal(str, str)


class ReactionBox(QGraphicsItem):
    """Handle to the line edits on the map"""

    def __init__(self, parent: MapView, key: int):
        QGraphicsItem.__init__(self)

        self.map = parent
        self.key = key

        self.item = QLineEdit()
        self.item.setMaximumWidth(80)
        self.item.setToolTip(
            self.map.appdata.project.maps[self.map.idx]["boxes"][key][2])
        self.proxy = self.map.scene.addWidget(self.item)
        self.proxy.show()

        palette = self.item.palette()
        palette.setColor(QPalette.Base, self.map.appdata.Defaultcolor)
        role = self.item.foregroundRole()
        palette.setColor(role, Qt.black)
        self.item.setPalette(palette)

        self.setCursor(Qt.OpenHandCursor)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.item.textEdited.connect(self.value_changed)
        self.item.returnPressed.connect(self.returnPressed)

        self.item.setContextMenuPolicy(Qt.CustomContextMenu)
        self.item.customContextMenuRequested.connect(self.on_context_menu)

        # create context menu
        self.popMenu = QMenu(parent)
        delete_action = QAction('remove from map', parent)
        self.popMenu.addAction(delete_action)
        delete_action.triggered.connect(self.delete)
        self.popMenu.addSeparator()

    def returnPressed(self):
        print(self.key, "return pressed to", self.item.text())
        if verify_value(self.item.text()):
            self.map.value_changed(self.key, self.item.text())

        # TODO: actually I want to repaint
        # self.map.update()

    def value_changed(self):
        print(self.key, "value changed to", self.item.text())
        test = self.item.text().replace(" ", "")
        if test == "":
            self.map.value_changed(self.key, test)
            self.set_color(self.map.appdata.Defaultcolor)
        elif verify_value(self.item.text()):
            self.map.value_changed(self.key, self.item.text())
            if self.key in self.map.appdata.project.scen_values.keys():
                self.set_color(self.map.appdata.Scencolor)
            else:
                self.set_color(self.map.appdata.Compcolor)
        else:
            self.set_color(Qt.magenta)

        # TODO: actually I want to repaint
        # self.map.update()

    def set_val_and_color(self, value: Tuple[float, float]):
        self.set_value(value)
        self.recolor()

    def set_value(self, value: Tuple[float, float]):
        (vl, vu) = value
        if isclose(vl, vu, abs_tol=self.map.appdata.abs_tol):
            # print("isclose", vl, round(vl, self.map.appdata.rounding),
            #   vu, round(vu, self.map.appdata.rounding))
            self.item.setText(str(round(vl, self.map.appdata.rounding)))
        else:
            # print("notclose", vl, round(vl, self.map.appdata.rounding),
            #       vu, round(vu, self.map.appdata.rounding))
            self.item.setText(
                str((round(vl, self.map.appdata.rounding), round(vu, self.map.appdata.rounding))))
        self.item.setCursorPosition(0)

    def recolor(self):
        value = self.item.text()
        test = value.replace(" ", "")
        if test == "":
            self.set_color(self.map.appdata.Defaultcolor)
        elif verify_value(value):
            if self.key in self.map.appdata.project.scen_values.keys():
                value = self.map.appdata.project.scen_values[self.key]

                # We differentiate special cases like (vl==vu)
                # try:
                #     x_ = float(value)
                #     self.set_color(self.map.appdata.Scencolor)
                # except:
                #     (vl, vu) = make_tuple(value)
                #     if math.isclose(vl, vu, abs_tol=self.map.appdata.abs_tol):
                #         self.set_color(self.map.appdata.Specialcolor)
                self.set_color(self.map.appdata.Scencolor)
            else:
                value = self.map.appdata.project.comp_values[self.key]
                (vl, vu) = value
                if math.isclose(vl, vu, abs_tol=self.map.appdata.abs_tol):
                    if len(self.map.appdata.project.modes) == 0:
                        self.set_color(self.map.appdata.Compcolor)
                    else:
                        if vl == 0:
                            self.set_color(Qt.red)
                        else:
                            self.set_color(Qt.green)
                else:
                    if vl <= 0 and vu >= 0:
                        self.set_color(self.map.appdata.SpecialColor1)
                    else:
                        self.set_color(self.map.appdata.SpecialColor2)
        else:
            self.set_color(Qt.magenta)

    def set_color(self, color: QColor):
        palette = self.item.palette()
        palette.setColor(QPalette.Base, color)
        role = self.item.foregroundRole()
        palette.setColor(role, Qt.black)
        self.item.setPalette(palette)

    def boundingRect(self):
        return QRectF(-15, -15, 20, 20)

    def paint(self, painter: QPainter, option, widget: QWidget):
        # painter.setPen(Qt.NoPen)
        # set color depending on wether the value belongs to the scenario
        if self.key in self.map.appdata.project.scen_values.keys():
            painter.setPen(Qt.magenta)
            painter.setBrush(Qt.magenta)
        else:
            # painter.setBrush(Qt.darkGray)
            painter.setPen(Qt.darkGray)
        # painter.drawEllipse(-15, -15, 20, 20)
        painter.drawRect(-15, -15, 20, 20)
        painter.setPen(Qt.darkGray)
        painter.drawLine(-5, 0, -5, -10)
        painter.drawLine(0, -5, -10,  -5)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        print("ReactionBox::mousePressedEvent")
        pass

    def mouseReleaseEvent(self, _event: QGraphicsSceneMouseEvent):
        print("ReactionBox::mouseReleaseEvent")
        pass

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent):
        drag = QDrag(event.widget())
        mime = QMimeData()
        mime.setText(str(self.key))
        drag.setMimeData(mime)
        # self.setCursor(Qt.ClosedHandCursor)
        drag.exec_()
        # self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        print("ReactionBox::double_clickEvent")
        self.map.emit_doubleClickedReaction(str(self.key))

    def setPos(self, x, y):
        self.proxy.setPos(x, y)
        super().setPos(x, y)

    def on_context_menu(self, point):
        # show context menu
        self.popMenu.exec_(self.item.mapToGlobal(point))

    def delete(self):
        # print('ReactionBox:delete')
        self.map.delete_box(self.key)


def verify_value(value):
    try:
        x = float(value)
    except:
        try:
            (vl, vh) = make_tuple(value)
            if not isinstance(vl, float):
                return False
            if not isinstance(vh, float):
                return False
        except:
            return False
        else:
            return True
    else:
        return True
