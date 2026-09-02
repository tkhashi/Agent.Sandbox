TOOLS = [
    {
        "name": "classify_perspective",
        "description": (
            "Analyze the image and classify the perspective type. "
            "perspective_type MUST be exactly one of: one_point, two_point, three_point, none. "
            "confidence MUST be exactly one of: high, low. "
            "scores: integer 0-100 for EACH type independently (do NOT need to sum to 100). "
            "reasoning: explain which visual features drove the decision and why other types were ruled out."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "perspective_type": {
                    "type": "string",
                    "enum": ["one_point", "two_point", "three_point", "none"],
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "low"],
                },
                "scores": {
                    "type": "object",
                    "description": "Confidence score 0-100 for each perspective type independently.",
                    "properties": {
                        "one_point":   {"type": "integer", "minimum": 0, "maximum": 100},
                        "two_point":   {"type": "integer", "minimum": 0, "maximum": 100},
                        "three_point": {"type": "integer", "minimum": 0, "maximum": 100},
                        "none":        {"type": "integer", "minimum": 0, "maximum": 100},
                    },
                    "required": ["one_point", "two_point", "three_point", "none"],
                },
                "reasoning": {
                    "type": "string",
                    "description": "Explain visual evidence for the chosen type and why others were ruled out.",
                },
            },
            "required": ["perspective_type", "confidence", "scores", "reasoning"],
        },
    },
    {
        "name": "detect_lines",
        "description": (
            "Detect line segments from the image using Canny edge detection + LSD. "
            "Call this after classify_perspective to extract geometric lines for VP estimation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "canny_low": {"type": "integer", "default": 50},
                "canny_high": {"type": "integer", "default": 150},
            },
        },
    },
    {
        "name": "find_vanishing_points",
        "description": (
            "Estimate vanishing points from detected lines using RANSAC clustering. "
            "num_vanishing_points MUST be 1, 2, or 3 matching the perspective type "
            "(one_point→1, two_point→2, three_point→3)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "num_vanishing_points": {"type": "integer", "enum": [1, 2, 3]},
            },
            "required": ["num_vanishing_points"],
        },
    },
    {
        "name": "retry_with_adjusted_params",
        "description": (
            "Retry line detection with adjusted Canny parameters when the previous result "
            "had too few lines or poor vanishing point accuracy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "canny_low": {"type": "integer"},
                "canny_high": {"type": "integer"},
            },
        },
    },
]
