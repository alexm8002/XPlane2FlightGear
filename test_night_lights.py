import hashlib
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET
from night_lights import parse_obj_lights,write_lights_ac,write_lights_xml,write_lit_xml,classify

class LightingTests(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(classify('taxi_b'),(0.06,0.28,1.0))
        self.assertEqual(classify('taxiway_blue'),(0.06,0.28,1.0))
        self.assertIsNone(classify('mystery_123'))

    def test_convert(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); src=root/'test.obj'
            src.write_text('''A\n800\nOBJ\nTEXTURE tex.dds\nTEXTURE_LIT tex_LIT.dds\nLIGHT_PARAM taxi_b 1 2 3 0.5\nLIGHT_NAMED runway_white 4 5 6\nLIGHT_NAMED abc_unknown 4 5 6\nLIGHT_SPILL_CUSTOM 1 2 3 1 1 1 1 50 0 -1 0 0.7 none\n''')
            lights,lit,c,unknown=parse_obj_lights(src)
            self.assertEqual(lit,'tex_LIT.dds')
            self.assertEqual(len(lights),3)
            self.assertEqual(c['LIGHT_PARAM'],1)
            self.assertEqual(unknown['LIGHT_SPILL_CUSTOM'],1)
            n=write_lights_ac(root/'lights.ac',lights)
            self.assertEqual(n,2)
            write_lights_xml(root/'lights.xml','lights.ac')
            self.assertEqual(ET.parse(root/'lights.xml').getroot().findtext('path'),'lights.ac')
            write_lit_xml(root/'lit.xml','model.ac','day.png','night.png')
            self.assertEqual(len(ET.parse(root/'lit.xml').getroot().findall('animation')),2)

if __name__ == '__main__':unittest.main()
