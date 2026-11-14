import unittest
from unittest.mock import MagicMock
from asset_service.building_editor_store import save_building_anchor_values
from asset_service.content_asset_store import ContentRevisionConflict
class BuildingAnchorStoreTests(unittest.TestCase):
    def test_anchor_only_preserves_size(self):
        current_database_connection=MagicMock()
        current_database_connection.execute.return_value.fetchone.side_effect=[{'current_generation':3},None,{'anchor_x':.5,'anchor_y':.8,'display_width_adjustment':20,'display_height_adjustment':10}]
        current_request_payload={'requestId':'test-anchor','expectedGeneration':3,'expected':{'anchorX':.5,'anchorY':.8},'properties':{'anchorX':.6,'anchorY':.8}}
        current_saved_response=save_building_anchor_values(current_database_connection,'ward-tower-v1',current_request_payload)
        self.assertEqual(current_saved_response['properties']['displayWidthAdjustment'],20)
        self.assertEqual(current_saved_response['properties']['displayHeightAdjustment'],10)
        self.assertEqual(current_saved_response['generation'],4)
    def test_stale_generation_rejects_write(self):
        current_database_connection=MagicMock()
        current_database_connection.execute.return_value.fetchone.side_effect=[{'current_generation':4},None]
        with self.assertRaises(ContentRevisionConflict):
            save_building_anchor_values(current_database_connection,'ward-tower-v1',{'requestId':'test-anchor','expectedGeneration':3,'expected':{'anchorX':.5,'anchorY':.8},'properties':{'anchorX':.6,'anchorY':.8}})
        self.assertEqual(current_database_connection.execute.call_count,2)
    def test_shape_and_placement_round_trip(self):
        from asset_service.content_building_properties import decode_building_property_record
        for current_shadow_shape in ('circle','rectangle'):
            current_database_connection=MagicMock()
            current_database_connection.execute.return_value.fetchone.side_effect=[{'current_generation':3},None,{'anchor_x':.5,'anchor_y':.8,'display_width_adjustment':0,'display_height_adjustment':0,'display_adjustments':{}}]
            current_property_values={'anchorX':.5,'anchorY':.8,'shadowShape':current_shadow_shape,'shadowOffsetColumn':-.4,'shadowOffsetRow':.3,'shadowScaleX':1.5,'shadowScaleY':.8}
            current_saved_response=save_building_anchor_values(current_database_connection,'ward-tower-v1',{'requestId':'test-shape','expectedGeneration':3,'expected':{'anchorX':.5,'anchorY':.8},'properties':current_property_values})
            current_stored_adjustments=current_database_connection.execute.call_args_list[3].args[1][2].obj
            current_decoded_properties=decode_building_property_record({'anchor_x':.5,'anchor_y':.8,'display_adjustments':current_stored_adjustments})
            self.assertEqual(current_decoded_properties,current_saved_response['properties'])
            self.assertEqual(current_decoded_properties['shadowShape'],current_shadow_shape)
    def test_invalid_shadow_rejected_before_database(self):
        from asset_service.content_building_properties import validate_building_property_values
        for current_invalid_shadow in ({'shadowShape':'triangle'},{'shadowShape':'circle','shadowOffsetColumn':5,'shadowOffsetRow':0,'shadowScaleX':1,'shadowScaleY':1},{'shadowShape':'circle','shadowOffsetColumn':0,'shadowOffsetRow':0,'shadowScaleX':0,'shadowScaleY':1}):
            with self.assertRaises(ValueError):validate_building_property_values({'anchorX':.5,'anchorY':.8,**current_invalid_shadow})
if __name__=='__main__':unittest.main()
