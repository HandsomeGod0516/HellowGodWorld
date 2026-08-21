import logging
import os
import pickle
from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional, Tuple, TypeVar, Union

import numpy as np
import pyproj
import shapely
import stringcase
from agentsociety2.contrib.env.mobility_space.utils import POI_CATG_DICT
from geojson import Feature
from google.protobuf import json_format
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import Message
from pycityproto.city.geo.v2 import geo_pb2
from pycityproto.city.map.v2 import map_pb2
from pycityproto.city.routing.v2 import routing_pb2
from pycityproto.city.routing.v2 import routing_service_pb2 as routing_service
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import substring, unary_union

__all__ = ["Map"]

# Generic
T = TypeVar("T", bound=Message)


def dict2pb(d: dict, pb: T) -> T:
    """
    Convert a Python dictionary to a protobuf message.

    Args:
    - d: The Python dict to be converted.
    - pb: The protobuf message to be filled.

    Returns:
    - The protobuf message.
    """
    return json_format.ParseDict(d, pb, ignore_unknown_fields=True)


class Map:
    """
    地圖API
    Map API
    """

    def __init__(
        self,
        pb_path: str,
    ):
        """
        Args:
        - pb_path (str): pb檔案路徑. pb file path.
        """
        logging.debug("Map init")
        map_data = None
        # 1. try to load from cache
        cache_path = pb_path + ".cache"
        if os.path.exists(cache_path):
            logging.debug("Start load cache file")
            with open(cache_path, "rb") as f:
                map_data = pickle.load(f)
            logging.debug("Finish load cache file")

        if map_data is None:
            logging.debug("No cache file found, start parse pb file")
            with open(pb_path, "rb") as f:
                pb = map_pb2.Map().FromString(f.read())
            jsons = []
            for field in pb.DESCRIPTOR.fields:
                class_name = stringcase.spinalcase(field.message_type.name)
                if field.label == field.LABEL_REPEATED:
                    for pb_field in getattr(pb, field.name):
                        data = MessageToDict(
                            pb_field,
                            always_print_fields_with_no_presence=True,
                            preserving_proto_field_name=True,
                            use_integers_for_enums=True,
                        )
                        jsons.append({"class": class_name, "data": data})
                else:
                    data = MessageToDict(
                        getattr(pb, field.name),
                        preserving_proto_field_name=True,
                        use_integers_for_enums=True,
                    )
                    jsons.append({"class": class_name, "data": data})
            map_data = self._parse_map(jsons)
            logging.debug("Finish parse pb file")
            logging.debug("Start save cache file")
            with open(cache_path, "wb") as f:
                pickle.dump(map_data, f)
            logging.debug("Finish save cache file")

        self.header: dict = map_data["header"]
        """
        地圖後設資料，包含如下屬性:
        Map metadata, including the following attributes:
        - name (string): 城市道路名稱，供標識資料集合的語義。Map name, to identify the semantics of data collections.
        - date (string): 城市道路資料的建立時間。Map data creation time.
        - north (float): 道路資料的北邊界座標。The coordinate of the northern boundary of the Map data.
        - south (float): 道路資料的南邊界座標。The coordinate of the southern boundary of the Map data.
        - east (float): 道路資料的東邊界座標。The coordinate of the eastern boundary of the Map data.
        - west (float): 道路資料的西邊界座標。The coordinate of the western boundary of the Map data.
        - projection (string): PROJ.4 投影字串，用以支援xy座標到其他座標系的轉換。PROJ.4 projection string to support the conversion of xy coordinates to other coordinate systems.
        """

        self.juncs: Dict[int, dict] = map_data["juncs"]
        """
        地圖中的路口集合（junction），字典的值包含如下屬性:
        The intersection collection (junction) in the map, the value of the dictionary contains the following attributes:
        - id (int): 路口編號。Junction ID.
        - lane_ids (list[int]): 屬於該路口的所有車道和人行道編號。IDs of all driving and pedestrian lanes belonging to this junction.
        - center (Dict[str, float]): 路口的大致中心點。The approximate center of the junction. example: {'x': 5983.14, 'y': 1807.73}
        """

        self.lanes: Dict[int, dict] = map_data["lanes"]
        """
        地圖中的車道集合（lane），字典的值包含如下屬性:
        The lane collection (lane) in the map. The value of the dictionary contains the following attributes:
        - id (int): 車道編號。Lane ID.
        - type (int): 車道型別 (1:行車|2:步行)。Lane type (1: Driving | 2: Pedestrian).
        - turn (int): 轉向型別 (1:直行|2:左轉|3: 右轉|4: 掉頭)。Turn type (1: straight | 2: left | 3: right | 4: around).
        - max_speed (float): 最大速度限制(單位: m/s)。Maximum speed limit (m/s).
        - length (float): 車道中心線的長度(單位: m)。Length of lane centerline (m).
        - width (float): 車道的寬度(單位: m)。Lane width.
        - center_line (list[XYPosition]): 車道中心線的形狀。Lane centerline shape.
        - predecessors (list[LaneConnection]): 前驅車道編號和連線型別。對於路口內的車道，最多隻有一個前驅車道。對於 LANE_TYPE_DRIVING，連線型別必須是 LANE_CONNECTION_TYPE_TAIL。對於 LANE_TYPE_WALKING，兩種連線型別都可能。ID and connection type of predecessor lanes. For lanes within a junction, there is at most one predecessor lane. For LANE_TYPE_DRIVING, the connection type must be LANE_CONNECTION_TYPE_TAIL. For LANE_TYPE_WALKING, both connection types are possible.
        - successors (list[LaneConnection]): 後繼車道編號和連線型別。對於路口內的車道，最多隻有一個後繼車道。對於 LANE_TYPE_DRIVING，連線型別必須是 LANE_CONNECTION_TYPE_HEAD。對於 LANE_TYPE_WALKING，兩種連線型別都可能。ID and connection type of successor lanes. For lanes within a junction, there is at most one successor lane. For LANE_TYPE_DRIVING, the connection type must be LANE_CONNECTION_TYPE_HEAD. For LANE_TYPE_WALKING, both connection types are possible.
        - left_lane_ids (list[int]): 左側相鄰車道的車道編號，從最近到最遠排列。Lane IDs of the adjacent lanes on the left, arranged from closest to furthest.
        - right_lane_ids (list[int]): 右側相鄰車道的車道編號，從最近到最遠排列。Lane IDs of the adjacent lanes on the right, arranged from closest to furthest.
        - parent_id (int): 車道所屬的道路/路口編號。The road/intersection ID to which the lane belongs.
        - aoi_ids (list[int]): 與車道連線的 AOI 編號。AOI IDs connected to the lane.
        - shapely_xy (shapely.geometry.LineString): 車道中心線的形狀（xy座標系）。Shape of lane centerline (in xy coordinates).
        - shapely_lnglat (shapely.geometry.LineString): 車道中心線的形狀（經緯度座標系）Shape of lane centerline (in latitude and longitude).
        """

        self.roads: Dict[int, dict] = map_data["roads"]
        """
        地圖中的道路集合（road），字典的值包含如下屬性:
        The road collection (road) in the map, the value of the dictionary contains the following attributes:
        - id (int): 道路編號。Road ID.
        - lane_ids (list[int]): 道路所包含的車道和人行道編號。Driving and pedestrian lane IDs that the road contains.
        """

        self.aois: Dict[int, dict] = map_data["aois"]
        """
        地圖中的AOI集合（aoi），字典的值包含如下屬性:
        AOI collection (aoi) in the map, the value of the dictionary contains the following attributes:
        - id (int): AOI編號。AOI ID.
        - positions (list[XYPosition]): 多邊形空間範圍。Shape of polygon.
        - area (float): 面積(單位: m2)。Area.
        - driving_positions (list[LanePosition]): 和道路網中行車道的連線點。Connection points to driving lanes.
        - walking_positions (list[LanePosition]): 和道路網中人行道的連線點。Connection points to pedestrian lanes.
        - driving_gates (list[XYPosition]): 和道路網中行車道的連線點對應的AOI邊界上的位置。Position on the AOI boundary corresponding to the connection point to driving lanes.
        - walking_gates (list[XYPosition]): 和道路網中人行道的連線點對應的AOI邊界上的位置。Position on the AOI boundary corresponding to the connection point to pedestrian lanes.
        - urban_land_use (Optional[str]): 城市建設用地分類，參照執行標準GB 50137-2011（https://www.planning.org.cn/law/uploads/2013/1383993139.pdf） Urban Land use type, refer to the national standard GB 50137-2011.
        - poi_ids (list[int]): 包含的POI列表。Contained POI IDs.
        - shapely_xy (shapely.geometry.Polygon): AOI的形狀（xy座標系）。Shape of polygon (in xy coordinates).
        - shapely_lnglat (shapely.geometry.Polygon): AOI的形狀（經緯度座標系）。Shape of polygon (in latitude and longitude).
        """

        self.pois: Dict[int, dict] = map_data["pois"]
        """
        地圖中的POI集合（poi），字典的值包含如下屬性:
        POI collection (poi) in the map, the value of the dictionary contains the following attributes:
        - id (int): POI編號。POI ID.
        - name (string): POI名稱。POI name.
        - category (list[string]): POI類別。POI category.
        - position (XYPosition): POI位置。POI position.
        - aoi_id (int): POI所屬的AOI編號。AOI ID to which the POI belongs.
        """

        self.projector: pyproj.Proj = map_data["projector"]
        """
        採用PROJ.4投影字串建立的轉換器，用以支援xy座標到WGS84座標系的轉換
        Converter created using PROJ.4 projection string to support conversion of xy coordinates to WGS84 coordinate system
        """
        (
            self._aoi_tree,
            self._aoi_list,
            self._poi_tree,
            self._poi_list,
            self._driving_lane_tree,
            self._driving_lane_list,
            self._walking_lane_tree,
            self._walking_lane_list,
        ) = self._build_geo_index()

        self.poi_cate = POI_CATG_DICT

    def _parse_map(self, m: List[Any]) -> Dict[str, Any]:
        # client = MongoClient(uri)
        # m = list(client[db][coll].find({}))
        logging.debug("Start parse map data")
        header = None
        juncs = {}
        roads = {}
        lanes = {}
        aois = {}
        pois = {}
        for d in m:
            if "_id" in d:
                del d["_id"]
            t = d["class"]
            data = d["data"]
            if t == "lane":
                lanes[data["id"]] = data
            elif t == "junction":
                juncs[data["id"]] = data
            elif t == "road":
                roads[data["id"]] = data
            elif t == "aoi":
                aois[data["id"]] = data
            elif t == "poi":
                pois[data["id"]] = data
            elif t == "header":
                header = data
        assert header is not None, "header is None"
        logging.debug("Finish parse map data - classify")
        projector = pyproj.Proj(header["projection"])  # type: ignore
        # 處理lane的Geos
        logging.debug("Start process lane geos")
        for lane in lanes.values():
            nodes = np.array(
                [[one["x"], one["y"]] for one in lane["center_line"]["nodes"]]
            )
            lane["shapely_xy"] = LineString(nodes)
            lngs, lats = projector(nodes[:, 0], nodes[:, 1], inverse=True)
            lane["shapely_lnglat"] = LineString(list(zip(lngs, lats)))
        logging.debug("Finish process lane geos")
        # 處理road的Geos和其他屬性
        logging.debug("Start process road geos")
        for road in roads.values():
            lane_ids = road["lane_ids"]
            driving_lane_ids = [lid for lid in lane_ids if lanes[lid]["type"] == 1]
            road["driving_lane_ids"] = driving_lane_ids
            center_lane_id = lane_ids[len(driving_lane_ids) // 2]
            center_lane = lanes[center_lane_id]
            road["length"] = center_lane["length"]
            road["max_speed"] = center_lane["max_speed"]
            road["shapely_xy"] = center_lane["shapely_xy"]
            road["shapely_lnglat"] = center_lane["shapely_lnglat"]
        logging.debug("Finish process road geos")
        # 處理Aoi的Geos
        logging.debug("Start process aoi geos")
        for aoi in aois.values():
            if "area" not in aoi:
                # 不是多邊形aoi
                aoi["shapely_xy"] = Point(
                    aoi["positions"][0]["x"], aoi["positions"][0]["y"]
                )
            else:
                aoi["shapely_xy"] = Polygon(
                    [(one["x"], one["y"]) for one in aoi["positions"]]
                )
            xys = np.array([[one["x"], one["y"]] for one in aoi["positions"]])
            lngs, lats = projector(xys[:, 0], xys[:, 1], inverse=True)
            lnglat_positions = list(zip(lngs, lats))
            if "area" not in aoi:
                aoi["shapely_lnglat"] = Point(lnglat_positions[0])
            else:
                aoi["shapely_lnglat"] = Polygon(lnglat_positions)
        logging.debug("Finish process aoi geos")
        # 處理Poi的Geos
        logging.debug("Start process poi geos")
        for poi in pois.values():
            poi["category"] = poi["category"].split("|")
            point = Point(poi["position"]["x"], poi["position"]["y"])
            poi["shapely_xy"] = point
            lng, lat = projector(point.x, point.y, inverse=True)
            poi["shapely_lnglat"] = Point([lng, lat])
        logging.debug("Finish process poi geos")
        # 為junction解算大致的中心點
        logging.debug("Start calculate junction center")
        for junc in juncs.values():
            lane_shapelys = [
                lanes[lane_id]["shapely_xy"] for lane_id in junc["lane_ids"]
            ]
            geos = unary_union(lane_shapelys)
            center = geos.centroid
            junc["center"] = {"x": center.x, "y": center.y}
            # 計算中心點的經緯度座標
            lng, lat = projector(center.x, center.y, inverse=True)
            junc["center_lnglat"] = {"lng": lng, "lat": lat}
        logging.debug("Finish calculate junction center")

        return {
            "header": header,
            "juncs": juncs,
            "roads": roads,
            "lanes": lanes,
            "aois": aois,
            "pois": pois,
            "projector": projector,
        }

    def _build_geo_index(self):
        # poi:
        # {
        #     "id": 700000000,
        #     "name": "天翼(網際網路手機賣場)",
        #     "category": "131300",
        #     "position": {
        #       "x": 448802.148620172,
        #       "y": 4412128.118718166
        #     },
        #     "aoi_id": 500018954,
        # }
        logging.debug("Start build geo index")
        aoi_list = list(self.aois.values())
        aoi_tree = shapely.STRtree([aoi["shapely_xy"] for aoi in aoi_list])
        poi_list = list(self.pois.values())
        poi_tree = shapely.STRtree([poi["shapely_xy"] for poi in poi_list])
        driving_lane_list = [
            lane for lane in self.lanes.values() if lane["type"] == 1  # driving
        ]
        driving_lane_tree = shapely.STRtree(
            [lane["shapely_xy"] for lane in driving_lane_list]
        )
        walking_lane_list = [
            lane for lane in self.lanes.values() if lane["type"] == 2  # walking
        ]
        walking_lane_tree = shapely.STRtree(
            [lane["shapely_xy"] for lane in walking_lane_list]
        )
        logging.debug("Finish build geo index")
        return (
            aoi_tree,
            aoi_list,
            poi_tree,
            poi_list,
            driving_lane_tree,
            driving_lane_list,
            walking_lane_tree,
            walking_lane_list,
        )

    def _get_lane_s(self, position: geo_pb2.Position, lane_id: int) -> float:
        """
        解算position對應的在lane_id上的s值
        Solve the s value corresponding to position on lane_id
        """
        # 處理起點處的截斷
        if position.HasField("aoi_position"):
            aoi_id = position.aoi_position.aoi_id
            aoi = self.aois[aoi_id]
            ss = [
                p["s"]
                for p in aoi["walking_positions"] + aoi["driving_positions"]
                if p["lane_id"] == lane_id
            ]
            assert len(ss) == 1, f"lane {lane_id} not found in aoi {aoi_id}"
            return ss[0]
        elif position.HasField("lane_position"):
            return position.lane_position.s
        else:
            assert False, f"position {position} has no valid field"

    def _get_driving_geo(self, road_id: int):
        """
        根據道路ID獲取幾何資訊對應的Lane ID和Lane的幾何資訊
        Obtain the Lane ID and Lane's geometric information corresponding to the geometric information based on the road ID.
        """
        road = self.roads[road_id]
        aoi_lane_id = road["driving_lane_ids"][-1]
        geo: LineString = road["shapely_xy"]
        return aoi_lane_id, geo

    def _get_walking_geo(self, segment: routing_pb2.WalkingRouteSegment):
        """
        根據步行路段（導航結果）獲取幾何資訊對應的Lane ID和Lane的幾何資訊
        Obtain the Lane ID and Lane's geometric information corresponding to the geometric information based on the walking path (navigation result).
        """
        lane_id = segment.lane_id
        direction = segment.moving_direction
        geo: LineString = self.lanes[lane_id]["shapely_xy"]
        if direction == routing_pb2.MOVING_DIRECTION_BACKWARD:
            geo = geo.reverse()
        return lane_id, geo

    def lnglat2xy(self, lng: float, lat: float) -> Tuple[float, float]:
        """
        經緯度轉xy座標
        Convert latitude and longitude to xy coordinates

        Args:
        - lng (float): 經度。longitude.
        - lat (float): 緯度。latitude.

        Returns:
        - Tuple[float, float]: xy座標。xy coordinates.
        """
        return self.projector(lng, lat)

    def xy2lnglat(self, x: float, y: float) -> Tuple[float, float]:
        """
        xy座標轉經緯度
        xy coordinates to longitude and latitude

        Args:
        - x (float): x座標。x coordinate.
        - y (float): y座標。y coordinate.

        Returns:
        - Tuple[float, float]: 經緯度。Longitude and latitude.

        """
        return self.projector(x, y, inverse=True)

    def position2xy(
        self, position: Union[geo_pb2.Position, Dict[str, Any]]
    ) -> Tuple[float, float]:
        """
        將position轉換為xy座標
        Convert position to xy coordinates
        """

        # 如果position是dict，則轉換為geo_pb2.Position
        if isinstance(position, dict):
            position = dict2pb(position, geo_pb2.Position())
        if position.HasField("aoi_position"):
            aoi_id = position.aoi_position.aoi_id
            aoi = self.aois[aoi_id]
            # 計算aoi的中心點
            center = aoi["shapely_xy"].centroid
            return center.x, center.y
        elif position.HasField("lane_position"):
            lane_id = position.lane_position.lane_id
            s = position.lane_position.s
            lane = self.lanes[lane_id]
            point = lane["shapely_xy"].interpolate(s)
            return point.x, point.y
        else:
            assert False, f"position {position} has no valid field"

    def get_header(self):
        """
        查詢header
        query header
        """
        return self.header

    def get_aoi(self, id: int, include_unused: bool = False) -> Optional[Any]:
        """
        查詢AOI
        query AOI

        Args:
        - id (int): AOI id
        - include_unused (bool, optional): 是否包含未使用或無效的AOI屬性. Defaults to False. Whether contains unused or invalid AOI attributes. Defaults to False.

        Returns:
        - Optional[Any]: AOI（經過複製後的dict）。AOI (copied dict).
        """
        doc = self.aois.get(id)
        if doc is None:
            return None
        doc = deepcopy(doc)
        if not include_unused:
            del doc["type"]
            if "external" in doc:
                if "driving_distances" in doc["external"]:
                    del doc["external"]["driving_distances"]
                if "driving_lane_project_point" in doc["external"]:
                    del doc["external"]["driving_lane_project_point"]
                if "walking_distances" in doc["external"]:
                    del doc["external"]["walking_distances"]
                if "walking_lane_project_point" in doc["external"]:
                    del doc["external"]["walking_lane_project_point"]
        return doc

    def get_poi(self, id: int, include_unused: bool = False) -> Optional[Any]:
        """
        查詢poi
        query poi

        Args:
        - id (int): poi id
        - include_unused (bool, optional): 是否包含未使用或無效的poi屬性. Defaults to False. Whether contains unused or invalid POI attributes. Defaults to False.

        Returns:
        - Optional[Any]: poi（經過複製後的dict）。POI (copied dict).
        """
        doc = self.pois.get(id)
        if doc is None:
            return None
        doc = deepcopy(doc)
        if not include_unused:
            ...
        return doc

    def get_lane(self, id: int, include_unused: bool = False) -> Optional[Any]:
        """
        查詢lane
        query lane

        Args:
        - id (int): lane id
        - include_unused (bool, optional): 是否包含未使用或無效的lane屬性. Defaults to False. Whether contains unused or invalid lane attributes. Defaults to False.

        Returns:
        - Optional[Any]: lane（經過複製後的dict）。Lane (copied dict).
        """
        doc = self.lanes.get(id)
        if doc is None:
            return None
        doc = deepcopy(doc)
        if not include_unused:
            if "left_border_line" in doc:
                del doc["left_border_line"]
            if "right_border_line" in doc:
                del doc["right_border_line"]
            if "overlaps" in doc:
                del doc["overlaps"]
        return doc

    def get_road(self, id: int, include_unused: bool = False) -> Optional[Any]:
        """
        查詢road
        query road

        Args:
        - id (int): road id
        - include_unused (bool, optional): 是否包含未使用或無效的road屬性. Defaults to False. Whether contains unused or invalid road attributes. Defaults to False.

        Returns:
        - Optional[Any]: road（經過複製後的dict）。Road (copied dict).
        """
        doc = self.roads.get(id)
        if doc is None:
            return None
        doc = deepcopy(doc)
        if not include_unused:
            ...
        return doc

    def get_junction(self, id: int, include_unused: bool = False) -> Optional[Any]:
        """
        查詢junction
        query junction

        Args:
        - id (int): junction id
        - include_unused (bool, optional): 是否包含未使用或無效的junction屬性. Defaults to False.  Whether contains unused or invalid junction attributes. Defaults to False.

        Returns:
        - Optional[Any]: junction（經過複製後的dict）。Junction (copied dict).
        """
        doc = self.juncs.get(id)
        if doc is None:
            return None
        doc = deepcopy(doc)
        if not include_unused:
            if "external" in doc:
                del doc["external"]
            if "driving_lane_groups" in doc:
                del doc["driving_lane_groups"]
        return doc

    def export_aoi_center_as_geojson(
        self,
        id: int,
        properties: Union[Dict[str, Any], Literal["auto"]] = "auto",
    ) -> dict:
        """
        匯出aoi中心點為geojson
        Export aoi center point as geojson

        Args:
        - id (int): aoi id
        - properties (Dict[str, Any] | str, optional): geojson的properties, 設定為"auto"時包含aoi類別與所含的poi列表. Defaults to {}. Geojson's properties, when set to "auto", the properties include aoi category and the list of contained poi. Defaults to {}.

        Returns:
        - dict: geojson格式的dict。dict in geojson format.
        """
        aoi = self.get_aoi(id)
        assert aoi is not None, f"aoi {id} not found"
        geometry = aoi["shapely_lnglat"].centroid
        if properties == "auto":
            properties = {
                "point_type": "aoi",
                "id": str(id),
                "aoi_type": str(aoi.get("land_use", 0)),
                "poi_ids": [str(pid) for pid in aoi["poi_ids"]],
            }
        feature = Feature(id=id, geometry=geometry, properties=properties)
        return dict(feature)

    def export_aoi_as_geojson(
        self, id: int, properties: Union[Dict[str, Any], Literal["auto"]] = "auto"
    ) -> dict:
        """
        匯出aoi為geojson
        Export aoi as geojson

        Args:
        - id (int): aoi id
        - properties (Dict[str, Any] | str, optional): geojson的properties, 設定為"auto"時包含aoi類別與所含的poi列表. Defaults to {}. Geojson's properties, when set to "auto", the properties include aoi category and the list of contained poi. Defaults to {}.

        Returns:
        - dict: geojson格式的dict。dict in geojson format.
        """
        aoi = self.get_aoi(id)
        assert aoi is not None, f"aoi {id} not found"
        geometry = aoi["shapely_lnglat"]
        if properties == "auto":
            properties = {
                "aoi_type": str(aoi.get("land_use", 0)),
                "poi_ids": [str(pid) for pid in aoi.get("poi_ids", [])],
            }
        feature = Feature(id=id, geometry=geometry, properties=properties)
        return dict(feature)

    def export_poi_as_geojson(
        self, id: int, properties: Union[Dict[str, Any], Literal["auto"]] = "auto"
    ) -> dict:
        """
        匯出poi為geojson
        Export poi as geojson

        Args:
        - id (int): poi id
        - properties (Dict[str, Any] | str, optional): geojson的properties, 設定為"auto"時包含poi類別、名稱. Defaults to "auto". Geojson's properties, when set to "auto", the properties include poi category and name. Defaults to "auto".

        Returns:
        - dict: geojson格式的dict. dict in geojson format.
        """
        poi = self.get_poi(id)
        assert poi is not None, f"poi {id} not found"
        geometry = poi["shapely_lnglat"]
        if properties == "auto":
            properties = {
                "point_type": "poi",
                "id": str(id),
                "poi_type": poi["category"],
                "name": poi["name"],
                "address": "",
            }
        feature = Feature(id=id, geometry=geometry, properties=properties)
        return dict(feature)

    def export_lane_as_geojson(
        self, id: int, properties: Union[Dict[str, Any], Literal["auto"]] = "auto"
    ) -> dict:
        """
        匯出lane為geojson
        geojson的properties. Defaults to {}.

        Args:
        - id (int): lane id
        - properties (Dict[str, Any], optional): geojson的properties. Defaults to "auto"（含lane的類別、轉向類別、父物件ID、最大車速）. geojson properties. Defaults to {}. (including lane type, turn type, parent object ID, maximum vehicle speed).

        Returns:
        - dict: geojson格式的dict。dict in geojson format.
        """
        lane = self.get_lane(id)
        assert lane is not None, f"lane {id} not found"
        geometry = lane["shapely_lnglat"]
        if properties == "auto":
            properties = {
                "id": str(id),
                "lane_type": str(lane["type"]),
                "lane_turn": str(lane["turn"]),
                "parent_id": str(lane["parent_id"]),
                "max_speed": lane["max_speed"],
            }
        feature = Feature(id=id, geometry=geometry, properties=properties)
        return dict(feature)

    def export_road_as_geojson(self, id: int, properties: Dict[str, Any] = {}) -> dict:
        """
        匯出road為geojson
        Export road as geojson

        Args:
        - id (int): road id
        - properties (Dict[str, Any], optional): geojson的properties. Defaults to {}. geojson properties. Defaults to {}.

        Returns:
        - dict: geojson格式的dict。dict in geojson format.
        """
        road = self.get_road(id)
        assert road is not None, f"road {id} not found"
        geometry = road["shapely_lnglat"]
        feature = Feature(id=id, geometry=geometry, properties=properties)
        return dict(feature)

    def _route_to_xys(
        self,
        route_req: Union[routing_service.GetRouteRequest, dict],
        route_res: Union[routing_service.GetRouteResponse, dict],
    ) -> np.ndarray:
        if not isinstance(route_req, routing_service.GetRouteRequest):
            route_req = ParseDict(route_req, routing_service.GetRouteRequest())
        if not isinstance(route_res, routing_service.GetRouteResponse):
            route_res = ParseDict(route_res, routing_service.GetRouteResponse())
        assert route_req.type in (
            routing_pb2.ROUTE_TYPE_DRIVING,
            routing_pb2.ROUTE_TYPE_WALKING,
        ), f"route type {route_req.type} not supported"
        is_walk = route_req.type == routing_pb2.ROUTE_TYPE_WALKING
        coordinates = []
        for journey in route_res.journeys:
            if is_walk:
                assert journey.type == routing_pb2.JOURNEY_TYPE_WALKING
            else:
                assert journey.type == routing_pb2.JOURNEY_TYPE_DRIVING
            # 處理起點處的截斷
            lane_id, geo = (
                self._get_walking_geo(journey.walking.route[0])
                if is_walk
                else self._get_driving_geo(journey.driving.road_ids[0])
            )
            start_s = self._get_lane_s(route_req.start, lane_id)
            if len(journey.walking.route) == 1:
                end_s = self._get_lane_s(route_req.end, lane_id)
                geo = substring(geo, start_s, end_s)
            else:
                geo = substring(geo, start_s, geo.length)
            coordinates += list(geo.coords)
            # 處理中間的路段
            if is_walk:
                for route in journey.walking.route[1:-1]:
                    _, geo = self._get_walking_geo(route)
                    coordinates += list(geo.coords)
            else:
                for road_id in journey.driving.road_ids[1:-1]:
                    _, geo = self._get_driving_geo(road_id)
                    coordinates += list(geo.coords)
            if len(journey.walking.route) > 1:
                # 處理終點處的截斷
                lane_id, geo = (
                    self._get_walking_geo(journey.walking.route[-1])
                    if is_walk
                    else self._get_driving_geo(journey.driving.road_ids[-1])
                )
                end_s = self._get_lane_s(route_req.end, lane_id)
                geo = substring(geo, 0, end_s)
                coordinates += list(geo.coords)
        if route_req.start.HasField("aoi_position"):
            aoi_center = self.aois[route_req.start.aoi_position.aoi_id][
                "shapely_xy"
            ].centroid.coords[0]
            coordinates = [aoi_center] + coordinates
        if route_req.end.HasField("aoi_position"):
            aoi_center = self.aois[route_req.end.aoi_position.aoi_id][
                "shapely_xy"
            ].centroid.coords[0]
            coordinates = coordinates + [aoi_center]
        coordinates = np.array(coordinates)
        return coordinates

    def export_route_as_geojson(
        self,
        route_req: Union[routing_service.GetRouteRequest, dict],
        route_res: Union[routing_service.GetRouteResponse, dict],
        properties: dict = {},
    ) -> dict:
        """
        匯出route為geojson
        Export route as geojson

        Args:
        - route_req (routing_service.GetRouteRequest): 請求導航的輸入引數。Input parameters for request navigation.
        - route_res (routing_service.GetRouteResponse): 請求導航的輸出結果。Output results for request navigation.
        - properties (dict, optional): geojson的properties. Defaults to {}. geojson properties. Defaults to {}.

        Returns:
        - dict: geojson格式的dict。dict in geojson format.
        """
        if not isinstance(route_req, routing_service.GetRouteRequest):
            route_req = ParseDict(route_req, routing_service.GetRouteRequest())
        if not isinstance(route_res, routing_service.GetRouteResponse):
            route_res = ParseDict(route_res, routing_service.GetRouteResponse())

        coordinates = self._route_to_xys(route_req, route_res)
        # xy -> lnglat
        lngs, lats = self.projector(coordinates[:, 0], coordinates[:, 1], inverse=True)
        geo = LineString(list(zip(lngs, lats)))
        feature = Feature(geometry=geo, properties=properties)
        return dict(feature)

    def estimate_route_time(
        self,
        route_req: Union[routing_service.GetRouteRequest, dict],
        route_res: Union[routing_service.GetRouteResponse, dict],
    ) -> float:
        """
        估算導航路線的時間
        Estimate navigation route time

        Args:
        - route_req (routing_service.GetRouteRequest): 請求導航的輸入引數。Input parameters for request navigation.
        - route_res (routing_service.GetRouteResponse): 請求導航的輸出結果。Output results for request navigation.
        - walking_speed (float, optional): 步行速度（單位：m/s）. Defaults to 1.1. Walking speed (unit: m/s). Defaults to 1.1.

        Returns:
        - float: 估算的時間（單位：s）。Estimated time (unit: s).
        """
        if not isinstance(route_req, routing_service.GetRouteRequest):
            route_req = ParseDict(route_req, routing_service.GetRouteRequest())
        if not isinstance(route_res, routing_service.GetRouteResponse):
            route_res = ParseDict(route_res, routing_service.GetRouteResponse())

        is_walk = route_req.type == routing_pb2.ROUTE_TYPE_WALKING
        if is_walk:
            return sum(j.walking.eta for j in route_res.journeys)
        else:
            return sum(j.driving.eta for j in route_res.journeys)

    def query_pois(
        self,
        center: Union[Tuple[float, float], Point],
        radius: Optional[float] = None,
        category: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Tuple[Any, float]]:
        """
        查詢center點指定半徑內類別滿足字首的poi（按距離排序）。Query the POIs whose categories satisfy the prefix within the specified radius of the center point (sorted by distance).

        Args:
        - center (x, y): 中心點（xy座標系）。Center point (xy coordinate system).
        - radius (float, optional): 半徑（單位：m）。如果不提供則返回所有的poi。Radius (unit: m).If not provided, all pois within the map will be returned.
        - category (str, optional): 類別要素，如實際類別為`('amenity', 'arts_centre')`，那麼匹配的可以為`'amenity'`或`'arts_centre'`。Category, if the actual category is `('amenity', 'arts_centre')`, then the matching can be `'amenity'` or `'arts_centre'`.
        - limit (int, optional): 最多返回的poi數量，按距離排序，近的優先（預設None）. The maximum number of POIs returned, sorted by distance, closest ones first (default to None).

        Returns:
        - Union[List[Tuple[Any, float]],List[Any]]: poi列表，每個元素為（poi, 距離）或者poi。poi list, each element is (poi, distance) or poi.
        """
        if not isinstance(center, Point):
            center = Point(center)
        if radius is None:
            poi_iter = self._poi_list
        else:
            indices = self._poi_tree.query(center.buffer(radius))
            poi_iter = (self._poi_list[index] for index in indices)

        pois = []
        for poi in poi_iter:
            if category is None or category in poi["category"]:
                distance = center.distance(poi["shapely_xy"])
                pois.append((poi, distance))
        # 按照距離排序
        pois = sorted(pois, key=lambda x: x[1])
        if limit is not None:
            pois = pois[:limit]
        return pois

    def query_aois(
        self,
        center: Union[Tuple[float, float], Point],
        radius: float,
        urban_land_uses: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Tuple[Any, float]]:
        """
        查詢center點指定半徑內城市用地滿足條件的aoi（按距離排序）。Query the AOIs whose urban land use within the specified radius of the center point meets the conditions (sorted by distance).

        Args:
        - center (x, y): 中心點（xy座標系）。Center point (xy coordinate system).
        - radius (float): 半徑（單位：m）。Radius (unit: m).
        - urban_land_uses (List[str], optional): 城市用地分類列表，參照執行標準GB 50137-2011（https://www.planning.org.cn/law/uploads/2013/1383993139.pdf）. Urban land use classification list, refer to the national standard GB 50137-2011.
        - limit (int, optional): 最多返回的aoi數量，按距離排序，近的優先（預設None）. The maximum number of AOIs returned, sorted by distance, closest ones first (default to None).

        Returns:
        - List[Tuple[Any, float]]: aoi列表，每個元素為（aoi, 距離）。aoi list, each element is (aoi, distance).
        """

        if not isinstance(center, Point):
            center = Point(center)
        # 獲取半徑內的aoi
        indices = self._aoi_tree.query(center.buffer(radius))
        # 過濾掉不滿足城市用地條件的aoi
        aois = []
        for index in indices:
            aoi = self._aoi_list[index]
            if (
                urban_land_uses is not None
                and aoi["urban_land_use"] not in urban_land_uses
            ):
                continue
            distance = center.distance(aoi["shapely_xy"])
            aois.append((aoi, distance))
        # 按照距離排序
        aois = sorted(aois, key=lambda x: x[1])
        if limit is not None:
            aois = aois[:limit]
        return aois

    def query_lane(
        self,
        xy: Union[Tuple[float, float], Point],
        radius: float,
        lane_type: int = 1,
    ):
        """
        查詢xy點指定半徑內的lane和s座標
        Query the lane and s coordinates within the specified radius of the xy point.

        Args:
        - xy (x, y): 中心點（xy座標系）。Center point (xy coordinate system).
        - radius (float): 半徑（單位：m），超出半徑則返回空列表。Radius (unit: m), if the radius is exceeded, an empty list will be returned.
        - lane_type (int): 車道型別（1:行車，預設|2:步行）。Lane type (1: driving, default | 2: walking).

        Returns:
        - List[Tuple[Any, float, float]]: lane列表，每個元素為（lane, s, 距離）。lane list, each element is (lane, s, distance).
        """

        if not isinstance(xy, Point):
            xy = Point(xy)
        if lane_type == 1:
            indices = self._driving_lane_tree.query(xy.buffer(radius))
            lanes = [self._driving_lane_list[index] for index in indices]
        elif lane_type == 2:
            indices = self._walking_lane_tree.query(xy.buffer(radius))
            lanes = [self._walking_lane_list[index] for index in indices]
        else:
            raise ValueError(f"lane_type {lane_type} not supported")
        result = []  # (lane, s, distance)
        # 計算距離和s座標
        for lane in lanes:
            distance = xy.distance(lane["shapely_xy"])
            if distance > radius:
                continue
            s = lane["shapely_xy"].project(xy)
            result.append((lane, s, distance))
        # 按距離排序
        result = sorted(result, key=lambda x: x[2])

        return result
