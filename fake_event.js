// https://docs.frigate.video/integrations/api/#get-apievents

const event = {
  "before": {
    "id": "1733152597.08157-flkir5",
    "camera": "driveway_camera",
    "frame_time": 1733152597.08157,
    "snapshot": null,
    "label": "person",
    "sub_label": null,
    "top_score": 0,
    "false_positive": true,
    "start_time": 1733152597.08157,
    "end_time": null,
    "score": 0.5859375,
    "box": [
      695,
      71,
      719,
      119
    ],
    "area": 1152,
    "ratio": 0.5,
    "region": [
      576,
      0,
      896,
      320
    ],
    "stationary": false,
    "motionless_count": 0,
    "position_changes": 0,
    "current_zones": [],
    "entered_zones": [],
    "has_clip": false,
    "has_snapshot": false,
    "attributes": {},
    "current_attributes": []
  },
  "after": {
    "id": "1733152597.08157-flkir5",
    "camera": "driveway_camera",
    "frame_time": 1733152600.076167,
    "snapshot": { // will this exist if has_snapshot is false?
      "frame_time": 1733152600.076167,
      "box": [
        542,
        14,
        588,
        104
      ],
      "area": 4140,
      "region": [
        432,
        0,
        752,
        320
      ],
      "score": 0.76953125,
      "attributes": []
    },
    "label": "person",
    "sub_label": null,
    "top_score": 0.724609375,
    "false_positive": false,
    "start_time": 1733152597.08157,
    "end_time": null,
    "score": 0.76953125,
    "box": [
      542,
      14,
      588,
      104
    ],
    "area": 4140,
    "ratio": 0.5111111111111111,
    "region": [
      432,
      0,
      752,
      320
    ],
    "stationary": false,
    "motionless_count": 0,
    "position_changes": 1,
    "current_zones": [
      "mailbox"
    ],
    "entered_zones": [
      "mailbox"
    ],
    "has_clip": true,
    "has_snapshot": true,
    "attributes": {},
    "current_attributes": []
  },
  "type": "new" // can be new. update, end
}