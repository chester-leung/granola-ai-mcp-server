"""Granola MCP Server implementation."""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
import zoneinfo
import time

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    TextContent,
    Tool,
)

from .models import CacheData, MeetingMetadata, MeetingDocument, MeetingTranscript


class GranolaMCPServer:
    """Granola MCP Server for meeting intelligence queries."""
    
    def __init__(self, cache_path: Optional[str] = None, timezone: Optional[str] = None):
        """Initialize the Granola MCP server."""
        if cache_path is None:
            cache_path = os.path.expanduser("~/Library/Application Support/Granola/cache-v3.json")
        
        self.cache_path = cache_path
        self.server = Server("granola-mcp-server")
        self.cache_data: Optional[CacheData] = None
        
        # Set up timezone handling
        if timezone:
            self.local_timezone = zoneinfo.ZoneInfo(timezone)
        else:
            # Auto-detect local timezone
            self.local_timezone = self._detect_local_timezone()
            
        self._setup_handlers()
    
    def _detect_local_timezone(self):
        """Detect the local timezone."""
        try:
            # Try to get system timezone
            if hasattr(time, 'tzname') and time.tzname:
                # Convert system timezone to zoneinfo
                # Common mappings for US timezones
                tz_mapping = {
                    'EST': 'America/New_York',
                    'EDT': 'America/New_York', 
                    'CST': 'America/Chicago',
                    'CDT': 'America/Chicago',
                    'MST': 'America/Denver',
                    'MDT': 'America/Denver',
                    'PST': 'America/Los_Angeles',
                    'PDT': 'America/Los_Angeles'
                }
                
                current_tz = time.tzname[time.daylight]
                if current_tz in tz_mapping:
                    return zoneinfo.ZoneInfo(tz_mapping[current_tz])
            
            # Fallback: try to detect from system offset
            local_offset = time.timezone if not time.daylight else time.altzone
            hours_offset = -local_offset // 3600
            
            # Common US timezone mappings by offset
            offset_mapping = {
                -8: 'America/Los_Angeles',  # PST
                -7: 'America/Denver',       # MST
                -6: 'America/Chicago',      # CST
                -5: 'America/New_York',     # EST
                -4: 'America/New_York'      # EDT (during daylight saving)
            }
            
            if hours_offset in offset_mapping:
                return zoneinfo.ZoneInfo(offset_mapping[hours_offset])
                
        except Exception as e:
            print(f"Error detecting timezone: {e}")
        
        # Ultimate fallback to Eastern Time (common for US business)
        return zoneinfo.ZoneInfo('America/New_York')
    
    def _convert_to_local_time(self, utc_datetime: datetime) -> datetime:
        """Convert UTC datetime to local timezone."""
        if utc_datetime.tzinfo is None:
            # Assume UTC if no timezone info
            utc_datetime = utc_datetime.replace(tzinfo=zoneinfo.ZoneInfo('UTC'))
        
        return utc_datetime.astimezone(self.local_timezone)
    
    def _format_local_time(self, utc_datetime: datetime) -> str:
        """Format datetime in local timezone for display."""
        local_dt = self._convert_to_local_time(utc_datetime)
        return local_dt.strftime('%Y-%m-%d %H:%M')

    def _parse_temporal_expressions(self, query: str) -> Tuple[Optional[datetime], Optional[datetime], bool]:
        """Parse temporal expressions from search query.

        Returns:
            Tuple of (start_date, end_date, sort_by_date)
            - start_date/end_date: datetime range to filter by (None if no date filter)
            - sort_by_date: whether to sort results by date (True for temporal queries)
        """
        query_lower = query.lower()
        now = datetime.now(self.local_timezone)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)


        # Convert to UTC for consistent filtering
        today_start_utc = today_start.astimezone(zoneinfo.ZoneInfo('UTC'))

        # Check for "most recent" or "latest" expressions
        if re.search(r'\b(most recent|latest|newest|recent)\b', query_lower):
            return None, None, True  # No date filter, but sort by date

        # Yesterday
        if re.search(r'\byesterday\b', query_lower):
            yesterday_start = today_start_utc - timedelta(days=1)
            yesterday_end = today_start_utc - timedelta(seconds=1)
            return yesterday_start, yesterday_end, True

        # Today
        if re.search(r'\btoday\b', query_lower):
            today_end = today_start_utc + timedelta(days=1) - timedelta(seconds=1)
            return today_start_utc, today_end, True

        # This week (Monday to Sunday)
        if re.search(r'\bthis week\b', query_lower):
            days_since_monday = today_start.weekday()
            week_start = (today_start - timedelta(days=days_since_monday)).astimezone(zoneinfo.ZoneInfo('UTC'))
            week_end = week_start + timedelta(days=7) - timedelta(seconds=1)
            return week_start, week_end, True

        # Last week
        if re.search(r'\blast week\b', query_lower):
            days_since_monday = today_start.weekday()
            this_week_start = today_start - timedelta(days=days_since_monday)
            last_week_start = (this_week_start - timedelta(days=7)).astimezone(zoneinfo.ZoneInfo('UTC'))
            last_week_end = this_week_start.astimezone(zoneinfo.ZoneInfo('UTC')) - timedelta(seconds=1)
            return last_week_start, last_week_end, True

        # This month
        if re.search(r'\bthis month\b', query_lower):
            month_start = today_start.replace(day=1).astimezone(zoneinfo.ZoneInfo('UTC'))
            # Calculate next month start
            if today_start.month == 12:
                next_month_start = today_start.replace(year=today_start.year + 1, month=1, day=1)
            else:
                next_month_start = today_start.replace(month=today_start.month + 1, day=1)
            month_end = next_month_start.astimezone(zoneinfo.ZoneInfo('UTC')) - timedelta(seconds=1)
            return month_start, month_end, True

        # Last month
        if re.search(r'\blast month\b', query_lower):
            # Calculate last month start
            if today_start.month == 1:
                last_month_start = today_start.replace(year=today_start.year - 1, month=12, day=1)
            else:
                last_month_start = today_start.replace(month=today_start.month - 1, day=1)

            month_start = today_start.replace(day=1)
            return (last_month_start.astimezone(zoneinfo.ZoneInfo('UTC')),
                   month_start.astimezone(zoneinfo.ZoneInfo('UTC')) - timedelta(seconds=1),
                   True)

        # Try to find specific date patterns (YYYY-MM-DD, MM/DD/YYYY, etc.)
        date_patterns = [
            r'\b(\d{4}-\d{1,2}-\d{1,2})\b',  # YYYY-MM-DD
            r'\b(\d{1,2}/\d{1,2}/\d{4})\b',  # MM/DD/YYYY
            r'\b(\d{1,2}-\d{1,2}-\d{4})\b',  # MM-DD-YYYY
        ]

        for pattern in date_patterns:
            match = re.search(pattern, query_lower)
            if match:
                date_str = match.group(1)
                try:
                    if '-' in date_str and len(date_str.split('-')[0]) == 4:  # YYYY-MM-DD
                        parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
                    elif '/' in date_str:  # MM/DD/YYYY
                        parsed_date = datetime.strptime(date_str, '%m/%d/%Y')
                    else:  # MM-DD-YYYY
                        parsed_date = datetime.strptime(date_str, '%m-%d-%Y')

                    # Convert to local timezone then to UTC
                    local_date = parsed_date.replace(tzinfo=self.local_timezone)
                    date_start_utc = local_date.astimezone(zoneinfo.ZoneInfo('UTC'))
                    date_end_utc = date_start_utc + timedelta(days=1) - timedelta(seconds=1)
                    return date_start_utc, date_end_utc, True
                except ValueError:
                    continue  # Try next pattern

        # Try to find month/day patterns (January 5, Dec 25, etc.)
        current_year = now.year
        month_day_patterns = [
            # Full month names
            r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?\b',
            # Abbreviated month names
            r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b',
            # Numeric patterns MM/DD (assume current year)
            r'\b(\d{1,2})/(\d{1,2})\b',
            # "on [date]" patterns
            r'\bon\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?\b',
            r'\bon\s+(\d{1,2})/(\d{1,2})\b',
        ]

        month_map = {
            'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
            'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6,
            'july': 7, 'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'sept': 9,
            'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12
        }

        for pattern in month_day_patterns:
            match = re.search(pattern, query_lower)
            if match:
                try:
                    groups = match.groups()
                    if len(groups) == 2:
                        if groups[0].isdigit():  # MM/DD format
                            month = int(groups[0])
                            day = int(groups[1])
                        else:  # Month name format
                            month = month_map.get(groups[0].lower())
                            day = int(groups[1])
                            if month is None:
                                continue

                        # Create date for current year first
                        try:
                            parsed_date = datetime(current_year, month, day)
                            local_date = parsed_date.replace(tzinfo=self.local_timezone)

                            # If the date is more than 6 months in the future, assume it was last year
                            # If the date is more than 6 months in the past, might be next year
                            date_diff = local_date - now
                            if date_diff.days > 180:
                                # Try previous year
                                parsed_date = datetime(current_year - 1, month, day)
                                local_date = parsed_date.replace(tzinfo=self.local_timezone)
                            elif date_diff.days < -180:
                                # Try next year
                                parsed_date = datetime(current_year + 1, month, day)
                                local_date = parsed_date.replace(tzinfo=self.local_timezone)

                            date_start_utc = local_date.astimezone(zoneinfo.ZoneInfo('UTC'))
                            date_end_utc = date_start_utc + timedelta(days=1) - timedelta(seconds=1)
                            return date_start_utc, date_end_utc, True

                        except ValueError:
                            continue  # Invalid date (e.g., Feb 30)

                except (ValueError, IndexError):
                    continue  # Try next pattern

        # No temporal expressions found
        return None, None, False

    def _filter_meetings_by_date(self, meetings: List[MeetingMetadata], start_date: Optional[datetime], end_date: Optional[datetime]) -> List[MeetingMetadata]:
        """Filter meetings by date range."""
        if start_date is None and end_date is None:
            return meetings

        filtered_meetings = []
        for meeting in meetings:
            meeting_date = meeting.date
            if start_date is not None and meeting_date < start_date:
                continue
            if end_date is not None and meeting_date > end_date:
                continue
            filtered_meetings.append(meeting)

        return filtered_meetings

    def _get_recent_meetings(self, meetings: List[MeetingMetadata], limit: int = 10) -> List[MeetingMetadata]:
        """Get the most recent meetings sorted by date."""
        sorted_meetings = sorted(meetings, key=lambda m: m.date, reverse=True)
        return sorted_meetings[:limit]

    def _clean_query_for_text_search(self, query: str) -> str:
        """Remove temporal expressions from query to get clean text for searching."""
        # Remove common temporal expressions that don't contribute to content search
        temporal_patterns = [
            r'\b(most recent|latest|newest|recent)\b',
            r'\byesterday\'?s?\b',  # Handle possessive forms
            r'\btoday\'?s?\b',
            r'\bthis week\'?s?\b',
            r'\blast week\'?s?\b',
            r'\bthis month\'?s?\b',
            r'\blast month\'?s?\b',
            # Specific date patterns
            r'\bfrom\s+(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4}|\d{1,2}-\d{1,2}-\d{4})\b',
            r'\bon\s+(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4}|\d{1,2}-\d{1,2}-\d{4})\b',
            # Month/day patterns
            r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?\b',
            r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b',
            r'\bon\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?\b',
            r'\bon\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b',
            r'\bon\s+(\d{1,2})/(\d{1,2})\b',
            # Remove standalone "on" when it precedes dates
            r'\bon\s+(?=(january|february|march|april|may|june|july|august|september|october|november|december|\d{1,2}/\d{1,2}))',
            # Standalone month/day numeric patterns (be careful not to match times)
            r'\b(\d{1,2})/(\d{1,2})\b(?!\d)',  # MM/DD but not MM/DD/YY or times like 12/12:30
        ]

        cleaned_query = query
        for pattern in temporal_patterns:
            cleaned_query = re.sub(pattern, '', cleaned_query, flags=re.IGNORECASE)

        # Clean up extra whitespace and apostrophes left behind
        cleaned_query = re.sub(r'\s+', ' ', cleaned_query)  # Multiple spaces to single space
        cleaned_query = re.sub(r"'\s*", ' ', cleaned_query)  # Orphaned apostrophes

        # Remove common leftover words that don't contribute to search
        leftover_patterns = [
            r'\b(meetings?|meeting|on|from|at|in|the|a|an)\s*$',  # End of string
            r'^\s*(meetings?|meeting|on|from|at|in|the|a|an)\b',  # Start of string
            r'\bmeetings?\s+on\b',  # "meetings on"
            r'\bmeetings?\s+from\b',  # "meetings from"
        ]

        for pattern in leftover_patterns:
            cleaned_query = re.sub(pattern, '', cleaned_query, flags=re.IGNORECASE)

        # Final whitespace cleanup
        cleaned_query = re.sub(r'\s+', ' ', cleaned_query).strip()
        return cleaned_query

    def _setup_handlers(self):
        """Set up MCP protocol handlers."""
        
        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            """List available tools."""
            return [
                Tool(
                    name="search_meetings",
                    description="Search meetings by title, content, participants, and date ranges. Supports natural language date expressions like 'yesterday', 'this week', 'most recent', etc.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query for meetings. Can include temporal expressions like 'most recent', 'yesterday', 'this week'"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results",
                                "default": 10
                            },
                            "sort_by_date": {
                                "type": "boolean",
                                "description": "Sort results by date instead of relevance (useful for temporal queries)",
                                "default": False
                            },
                            "date_range": {
                                "type": "object",
                                "properties": {
                                    "start_date": {"type": "string", "format": "date"},
                                    "end_date": {"type": "string", "format": "date"}
                                },
                                "description": "Optional explicit date range for filtering meetings"
                            }
                        },
                        "required": ["query"]
                    }
                ),
                Tool(
                    name="get_meeting_details",
                    description="Get detailed information about a specific meeting",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "meeting_id": {
                                "type": "string",
                                "description": "Meeting ID to retrieve details for"
                            }
                        },
                        "required": ["meeting_id"]
                    }
                ),
                Tool(
                    name="get_meeting_transcript",
                    description="Get transcript for a specific meeting",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "meeting_id": {
                                "type": "string", 
                                "description": "Meeting ID to get transcript for"
                            }
                        },
                        "required": ["meeting_id"]
                    }
                ),
                Tool(
                    name="get_meeting_documents",
                    description="Get documents associated with a meeting",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "meeting_id": {
                                "type": "string",
                                "description": "Meeting ID to get documents for" 
                            }
                        },
                        "required": ["meeting_id"]
                    }
                ),
                Tool(
                    name="analyze_meeting_patterns",
                    description="Analyze patterns across multiple meetings",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "pattern_type": {
                                "type": "string",
                                "description": "Type of pattern to analyze (topics, participants, frequency)",
                                "enum": ["topics", "participants", "frequency"]
                            },
                            "date_range": {
                                "type": "object",
                                "properties": {
                                    "start_date": {"type": "string", "format": "date"},
                                    "end_date": {"type": "string", "format": "date"}
                                },
                                "description": "Optional date range for analysis"
                            }
                        },
                        "required": ["pattern_type"]
                    }
                )
            ]
        
        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
            """Handle tool calls."""
            await self._ensure_cache_loaded()
            
            if name == "search_meetings":
                return await self._search_meetings(
                    query=arguments["query"],
                    limit=arguments.get("limit", 10),
                    sort_by_date=arguments.get("sort_by_date", False),
                    date_range=arguments.get("date_range")
                )
            elif name == "get_meeting_details":
                return await self._get_meeting_details(arguments["meeting_id"])
            elif name == "get_meeting_transcript":
                return await self._get_meeting_transcript(arguments["meeting_id"])
            elif name == "get_meeting_documents":
                return await self._get_meeting_documents(arguments["meeting_id"])
            elif name == "analyze_meeting_patterns":
                return await self._analyze_meeting_patterns(
                    pattern_type=arguments["pattern_type"],
                    date_range=arguments.get("date_range")
                )
            else:
                raise ValueError(f"Unknown tool: {name}")
    
    async def _ensure_cache_loaded(self):
        """Ensure cache data is loaded."""
        if self.cache_data is None:
            await self._load_cache()
    
    async def _load_cache(self):
        """Load and parse Granola cache data."""
        try:
            cache_path = Path(self.cache_path)
            if not cache_path.exists():
                self.cache_data = CacheData()
                return
            
            with open(cache_path, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
            
            # Handle Granola's nested cache structure
            if 'cache' in raw_data and isinstance(raw_data['cache'], str):
                # Cache data is stored as a JSON string inside the 'cache' key
                actual_data = json.loads(raw_data['cache'])
                if 'state' in actual_data:
                    raw_data = actual_data['state']
                else:
                    raw_data = actual_data
            
            self.cache_data = await self._parse_cache_data(raw_data)
            
        except Exception as e:
            self.cache_data = CacheData()
            print(f"Error loading cache: {e}")
    
    async def _parse_cache_data(self, raw_data: Dict[str, Any]) -> CacheData:
        """Parse raw cache data into structured models."""
        cache_data = CacheData()
        
        # Parse Granola documents (which are meetings)
        if "documents" in raw_data:
            for meeting_id, meeting_data in raw_data["documents"].items():
                try:
                    # Extract participants from people array
                    participants = []
                    if "people" in meeting_data and isinstance(meeting_data["people"], list):
                        participants = [person.get("name", "") for person in meeting_data["people"] if person.get("name")]
                    
                    # Parse creation date
                    created_at = meeting_data.get("created_at")
                    if created_at:
                        # Handle Granola's ISO format
                        if created_at.endswith('Z'):
                            created_at = created_at[:-1] + '+00:00'
                        naive_date = datetime.fromisoformat(created_at)
                        # Ensure timezone-aware datetime (assume UTC if naive)
                        if naive_date.tzinfo is None:
                            meeting_date = naive_date.replace(tzinfo=zoneinfo.ZoneInfo('UTC'))
                        else:
                            meeting_date = naive_date
                    else:
                        meeting_date = datetime.now(zoneinfo.ZoneInfo('UTC'))
                    
                    metadata = MeetingMetadata(
                        id=meeting_id,
                        title=meeting_data.get("title", "Untitled Meeting"),
                        date=meeting_date,
                        duration=None,  # Granola doesn't store duration in this format
                        participants=participants,
                        meeting_type=meeting_data.get("type", "meeting"),
                        platform=None  # Not stored in Granola cache
                    )
                    cache_data.meetings[meeting_id] = metadata
                except Exception as e:
                    print(f"Error parsing meeting {meeting_id}: {e}")
        
        # Parse Granola transcripts (list format)
        if "transcripts" in raw_data:
            for transcript_id, transcript_data in raw_data["transcripts"].items():
                try:
                    # Use transcript_id as meeting_id (they match in Granola)
                    meeting_id = transcript_id
                    
                    # Extract transcript content and speakers
                    content_parts = []
                    speakers_set = set()
                    
                    if isinstance(transcript_data, list):
                        # Granola format: list of speech segments
                        for segment in transcript_data:
                            if isinstance(segment, dict) and "text" in segment:
                                text = segment["text"].strip()
                                if text:
                                    content_parts.append(text)
                                
                                # Extract speaker info if available
                                if "source" in segment:
                                    speakers_set.add(segment["source"])
                    
                    elif isinstance(transcript_data, dict):
                        # Fallback: dict format (legacy or different structure)
                        if "content" in transcript_data:
                            content_parts.append(transcript_data["content"])
                        elif "text" in transcript_data:
                            content_parts.append(transcript_data["text"])
                        elif "transcript" in transcript_data:
                            content_parts.append(transcript_data["transcript"])
                        
                        # Extract speakers if available
                        if "speakers" in transcript_data:
                            speakers_set.update(transcript_data["speakers"])
                    
                    # Combine all content and create transcript
                    if content_parts:
                        full_content = " ".join(content_parts)
                        speakers_list = list(speakers_set) if speakers_set else []
                        
                        transcript = MeetingTranscript(
                            meeting_id=meeting_id,
                            content=full_content,
                            speakers=speakers_list,
                            language=None,  # Not typically stored in segment format
                            confidence=None  # Would need to be calculated from segments
                        )
                        cache_data.transcripts[meeting_id] = transcript
                        
                except Exception as e:
                    print(f"Error parsing transcript {transcript_id}: {e}")
        
        # Extract document content from Granola documents
        document_panels = raw_data.get("documentPanels", {})
        parse_panels = os.getenv("GRANOLA_PARSE_PANELS", "1") != "0"

        if "documents" in raw_data:
            for doc_id, doc_data in raw_data["documents"].items():
                try:
                    # Extract content from various Granola fields
                    content_parts = []
                    
                    # Try notes_plain first (cleanest format)
                    if doc_data.get("notes_plain"):
                        content_parts.append(doc_data["notes_plain"])
                    
                    # Try notes_markdown as backup
                    elif doc_data.get("notes_markdown"):
                        content_parts.append(doc_data["notes_markdown"])
                    
                    # Try to extract from structured notes field
                    elif doc_data.get("notes") and isinstance(doc_data["notes"], dict):
                        notes_content = self._extract_structured_notes(doc_data["notes"])
                        if notes_content:
                            content_parts.append(notes_content)
                    
                    # Fallback to document panels when traditional fields are empty
                    if parse_panels and not any(isinstance(part, str) and part.strip() for part in content_parts):
                        panel_text = self._extract_document_panel_content(document_panels.get(doc_id))
                        if panel_text:
                            content_parts.append(panel_text)

                    # Add overview if available
                    if doc_data.get("overview"):
                        content_parts.append(f"Overview: {doc_data['overview']}")
                    
                    # Add summary if available  
                    if doc_data.get("summary"):
                        content_parts.append(f"Summary: {doc_data['summary']}")
                    
                    content = "\n\n".join(content_parts)
                    
                    # Only create document if we have a meeting for it
                    if doc_id in cache_data.meetings:
                        meeting = cache_data.meetings[doc_id]
                        document = MeetingDocument(
                            id=doc_id,
                            meeting_id=doc_id,
                            title=meeting.title,
                            content=content,
                            document_type="meeting_notes",
                            created_at=meeting.date,
                            tags=[]
                        )
                        cache_data.documents[doc_id] = document
                        
                except Exception as e:
                    print(f"Error extracting document content for {doc_id}: {e}")
        
        cache_data.last_updated = datetime.now(zoneinfo.ZoneInfo('UTC'))
        return cache_data
    
    def _extract_structured_notes(self, notes_data: Dict[str, Any]) -> str:
        """Extract text content from Granola's structured notes format."""
        try:
            if not isinstance(notes_data, dict) or 'content' not in notes_data:
                return ""
            
            def extract_text_from_content(content_list):
                text_parts = []
                if isinstance(content_list, list):
                    for item in content_list:
                        if isinstance(item, dict):
                            # Handle different content types
                            if item.get('type') == 'paragraph' and 'content' in item:
                                text_parts.append(extract_text_from_content(item['content']))
                            elif item.get('type') == 'text' and 'text' in item:
                                text_parts.append(item['text'])
                            elif 'content' in item:
                                text_parts.append(extract_text_from_content(item['content']))
                return ' '.join(text_parts)
            
            return extract_text_from_content(notes_data['content'])
            
        except Exception as e:
            print(f"Error extracting structured notes: {e}")
            return ""

    def _extract_document_panel_content(self, panel_data: Any) -> str:
        """Extract text content from Granola's documentPanels structure."""
        if not panel_data:
            return ""

        text_parts = []

        def extract_from_node(node: Any):
            if isinstance(node, dict):
                node_type = node.get('type')

                if node_type == 'text' and node.get('text'):
                    text_parts.append(node['text'])
                elif 'content' in node:
                    extract_from_node(node['content'])
            elif isinstance(node, list):
                for item in node:
                    extract_from_node(item)

        try:
            if isinstance(panel_data, dict):
                # Panels keyed by UUID -> {content: [...]} structure
                for panel_id in sorted(panel_data.keys()):
                    panel = panel_data.get(panel_id)
                    if isinstance(panel, dict):
                        extract_from_node(panel.get('content'))
            elif isinstance(panel_data, list):
                for panel in panel_data:
                    extract_from_node(panel)

        except Exception as exc:
            print(f"Error extracting panel content: {exc}")

        combined = '\n\n'.join(part.strip() for part in text_parts if isinstance(part, str) and part.strip())
        return combined.strip()
    
    async def _search_meetings(self, query: str, limit: int = 10, sort_by_date: bool = False, date_range: Optional[Dict] = None) -> List[TextContent]:
        """Search meetings by query with date-aware functionality."""
        if not self.cache_data:
            return [TextContent(type="text", text="No meeting data available")]

        # Parse temporal expressions from the query
        parsed_start_date, parsed_end_date, auto_sort_by_date = self._parse_temporal_expressions(query)

        # Determine final date filtering and sorting behavior
        # Our temporal expression parsing takes precedence over external date_range
        # because we handle timezone calculations correctly
        if parsed_start_date is not None or parsed_end_date is not None:
            # Use our parsed dates - we calculated them correctly
            start_date = parsed_start_date
            end_date = parsed_end_date
        elif date_range:
            # Only use external date_range if we didn't parse any temporal expressions
            if date_range.get("start_date"):
                start_date = datetime.fromisoformat(date_range["start_date"]).replace(tzinfo=zoneinfo.ZoneInfo('UTC'))
            else:
                start_date = None

            if date_range.get("end_date"):
                end_date = datetime.fromisoformat(date_range["end_date"]).replace(tzinfo=zoneinfo.ZoneInfo('UTC'))
            else:
                end_date = None
        else:
            start_date = None
            end_date = None

        # Sort by date if explicitly requested or auto-detected from temporal query
        should_sort_by_date = sort_by_date or auto_sort_by_date

        # Get all meetings and apply date filtering
        meetings = list(self.cache_data.meetings.values())
        if start_date is not None or end_date is not None:
            meetings = self._filter_meetings_by_date(meetings, start_date, end_date)

        # Clean the query for text search (remove temporal expressions)
        clean_query = self._clean_query_for_text_search(query)

        # Determine query type
        is_date_only_query = not clean_query.strip()
        is_date_filtered_query = (start_date is not None or end_date is not None) and clean_query.strip()

        if is_date_only_query:
            # Pure date queries: "meetings from yesterday", "January 5", "most recent"
            if should_sort_by_date:
                # For "most recent" type queries, sort by date and return recent meetings
                meetings = self._get_recent_meetings(meetings, limit)
            else:
                # For specific date ranges, just limit the results
                meetings = meetings[:limit]

            results = [(1, meeting) for meeting in meetings]  # Assign equal relevance

        elif is_date_filtered_query:
            # Mixed queries: "backlog meetings from yesterday", "standup on January 5"
            # Search content within the date-filtered meetings
            query_lower = clean_query.lower()
            scored_results = []

            for meeting in meetings:
                score = 0

                # Search in title
                if query_lower in meeting.title.lower():
                    score += 3  # Higher weight for title matches

                # Search in participants
                for participant in meeting.participants:
                    if query_lower in participant.lower():
                        score += 2

                # Search in transcript content if available
                meeting_id = meeting.id
                if meeting_id in self.cache_data.transcripts:
                    transcript = self.cache_data.transcripts[meeting_id]
                    if query_lower in transcript.content.lower():
                        score += 1

                # Search in document content if available
                if meeting_id in self.cache_data.documents:
                    document = self.cache_data.documents[meeting_id]
                    if query_lower in document.content.lower():
                        score += 1

                # Only include meetings that have content matches
                if score > 0:
                    scored_results.append((score, meeting))

            # Sort by relevance, with date as secondary sort for ties
            if should_sort_by_date:
                scored_results.sort(key=lambda x: (x[0], x[1].date), reverse=True)
            else:
                scored_results.sort(key=lambda x: x[0], reverse=True)

            results = scored_results[:limit]

        else:
            # Pure content queries: "backlog meetings", "standup with Alice"
            # Search all meetings without date filtering
            query_lower = clean_query.lower()
            scored_results = []

            for meeting in meetings:
                score = 0

                # Search in title
                if query_lower in meeting.title.lower():
                    score += 3

                # Search in participants
                for participant in meeting.participants:
                    if query_lower in participant.lower():
                        score += 2

                # Search in transcript content if available
                meeting_id = meeting.id
                if meeting_id in self.cache_data.transcripts:
                    transcript = self.cache_data.transcripts[meeting_id]
                    if query_lower in transcript.content.lower():
                        score += 1

                # Search in document content if available
                if meeting_id in self.cache_data.documents:
                    document = self.cache_data.documents[meeting_id]
                    if query_lower in document.content.lower():
                        score += 1

                if score > 0:
                    scored_results.append((score, meeting))

            # Sort by relevance or date
            if should_sort_by_date:
                scored_results.sort(key=lambda x: (x[1].date, x[0]), reverse=True)
            else:
                scored_results.sort(key=lambda x: x[0], reverse=True)

            results = scored_results[:limit]

        # Generate output
        if not results:
            date_context = ""
            if start_date or end_date:
                if start_date and end_date:
                    date_context = f" between {self._format_local_time(start_date)} and {self._format_local_time(end_date)}"
                elif start_date:
                    date_context = f" after {self._format_local_time(start_date)}"
                elif end_date:
                    date_context = f" before {self._format_local_time(end_date)}"

            return [TextContent(type="text", text=f"No meetings found matching '{query}'{date_context}")]

        # Prepare output based on query type
        if not clean_query.strip() and should_sort_by_date:
            output_lines = [f"Most recent meetings"]
            if start_date or end_date:
                if start_date and end_date:
                    output_lines[0] += f" between {self._format_local_time(start_date)} and {self._format_local_time(end_date)}"
                elif start_date:
                    output_lines[0] += f" after {self._format_local_time(start_date)}"
                elif end_date:
                    output_lines[0] += f" before {self._format_local_time(end_date)}"
            output_lines[0] += ":\n"
        else:
            output_lines = [f"Found {len(results)} meeting(s) matching '{query}'"]
            if start_date or end_date:
                if start_date and end_date:
                    output_lines[0] += f" between {self._format_local_time(start_date)} and {self._format_local_time(end_date)}"
                elif start_date:
                    output_lines[0] += f" after {self._format_local_time(start_date)}"
                elif end_date:
                    output_lines[0] += f" before {self._format_local_time(end_date)}"
            output_lines[0] += ":\n"

        for score, meeting in results:
            output_lines.append(f"• **{meeting.title}** ({meeting.id})")
            output_lines.append(f"  Date: {self._format_local_time(meeting.date)}")
            if meeting.participants:
                output_lines.append(f"  Participants: {', '.join(meeting.participants)}")
            output_lines.append("")

        return [TextContent(type="text", text="\n".join(output_lines))]
    
    async def _get_meeting_details(self, meeting_id: str) -> List[TextContent]:
        """Get detailed meeting information."""
        if not self.cache_data or meeting_id not in self.cache_data.meetings:
            return [TextContent(type="text", text=f"Meeting '{meeting_id}' not found")]
        
        meeting = self.cache_data.meetings[meeting_id]
        
        details = [
            f"# Meeting Details: {meeting.title}\n",
            f"**ID:** {meeting.id}",
            f"**Date:** {self._format_local_time(meeting.date)}",
        ]
        
        if meeting.duration:
            details.append(f"**Duration:** {meeting.duration} minutes")
        
        if meeting.participants:
            details.append(f"**Participants:** {', '.join(meeting.participants)}")
        
        if meeting.meeting_type:
            details.append(f"**Type:** {meeting.meeting_type}")
        
        if meeting.platform:
            details.append(f"**Platform:** {meeting.platform}")
        
        # Add document count
        doc_count = sum(1 for doc in self.cache_data.documents.values() 
                       if doc.meeting_id == meeting_id)
        if doc_count > 0:
            details.append(f"**Documents:** {doc_count}")
        
        # Add transcript availability
        if meeting_id in self.cache_data.transcripts:
            details.append("**Transcript:** Available")
        
        return [TextContent(type="text", text="\n".join(details))]
    
    async def _get_meeting_transcript(self, meeting_id: str) -> List[TextContent]:
        """Get meeting transcript."""
        if not self.cache_data:
            return [TextContent(type="text", text="No meeting data available")]
        
        if meeting_id not in self.cache_data.transcripts:
            return [TextContent(type="text", text=f"No transcript available for meeting '{meeting_id}'")]
        
        transcript = self.cache_data.transcripts[meeting_id]
        meeting = self.cache_data.meetings.get(meeting_id)
        
        output = [f"# Transcript: {meeting.title if meeting else meeting_id}\n"]
        
        if transcript.speakers:
            output.append(f"**Speakers:** {', '.join(transcript.speakers)}")
        
        if transcript.language:
            output.append(f"**Language:** {transcript.language}")
        
        if transcript.confidence:
            output.append(f"**Confidence:** {transcript.confidence:.2%}")
        
        output.append("\n## Transcript Content\n")
        output.append(transcript.content)
        
        return [TextContent(type="text", text="\n".join(output))]
    
    async def _get_meeting_documents(self, meeting_id: str) -> List[TextContent]:
        """Get meeting documents."""
        if not self.cache_data:
            return [TextContent(type="text", text="No meeting data available")]
        
        documents = [doc for doc in self.cache_data.documents.values() 
                    if doc.meeting_id == meeting_id]
        
        if not documents:
            return [TextContent(type="text", text=f"No documents found for meeting '{meeting_id}'")]
        
        meeting = self.cache_data.meetings.get(meeting_id)
        output = [f"# Documents: {meeting.title if meeting else meeting_id}\n"]
        output.append(f"Found {len(documents)} document(s):\n")
        
        for doc in documents:
            output.append(f"## {doc.title}")
            output.append(f"**Type:** {doc.document_type}")
            output.append(f"**Created:** {self._format_local_time(doc.created_at)}")
            
            if doc.tags:
                output.append(f"**Tags:** {', '.join(doc.tags)}")
            
            output.append(f"\n{doc.content}\n")
            output.append("---\n")
        
        return [TextContent(type="text", text="\n".join(output))]
    
    async def _analyze_meeting_patterns(self, pattern_type: str, date_range: Optional[Dict] = None) -> List[TextContent]:
        """Analyze patterns across meetings."""
        if not self.cache_data:
            return [TextContent(type="text", text="No meeting data available")]
        
        meetings = list(self.cache_data.meetings.values())
        
        # Filter by date range if provided
        if date_range:
            start_date_str = date_range.get("start_date", "1900-01-01")
            end_date_str = date_range.get("end_date", "2100-01-01")
            
            # Parse dates and ensure timezone-aware
            naive_start = datetime.fromisoformat(start_date_str)
            naive_end = datetime.fromisoformat(end_date_str)
            
            # Localize naive datetimes to UTC
            if naive_start.tzinfo is None:
                start_date = naive_start.replace(tzinfo=zoneinfo.ZoneInfo('UTC'))
            else:
                start_date = naive_start
                
            if naive_end.tzinfo is None:
                end_date = naive_end.replace(tzinfo=zoneinfo.ZoneInfo('UTC'))
            else:
                end_date = naive_end
                
            meetings = [m for m in meetings if start_date <= m.date <= end_date]
        
        if pattern_type == "participants":
            return await self._analyze_participant_patterns(meetings)
        elif pattern_type == "frequency":
            return await self._analyze_frequency_patterns(meetings)
        elif pattern_type == "topics":
            return await self._analyze_topic_patterns(meetings)
        else:
            return [TextContent(type="text", text=f"Unknown pattern type: {pattern_type}")]
    
    async def _analyze_participant_patterns(self, meetings: List[MeetingMetadata]) -> List[TextContent]:
        """Analyze participant patterns."""
        participant_counts = {}
        
        for meeting in meetings:
            for participant in meeting.participants:
                participant_counts[participant] = participant_counts.get(participant, 0) + 1
        
        if not participant_counts:
            return [TextContent(type="text", text="No participant data found")]
        
        sorted_participants = sorted(participant_counts.items(), key=lambda x: x[1], reverse=True)
        
        output = [
            f"# Participant Analysis ({len(meetings)} meetings)\n",
            "## Most Active Participants\n"
        ]
        
        for participant, count in sorted_participants[:10]:
            output.append(f"• **{participant}:** {count} meetings")
        
        return [TextContent(type="text", text="\n".join(output))]
    
    async def _analyze_frequency_patterns(self, meetings: List[MeetingMetadata]) -> List[TextContent]:
        """Analyze meeting frequency patterns."""
        if not meetings:
            return [TextContent(type="text", text="No meetings found for analysis")]
        
        # Group by month
        monthly_counts = {}
        for meeting in meetings:
            month_key = meeting.date.strftime("%Y-%m")
            monthly_counts[month_key] = monthly_counts.get(month_key, 0) + 1
        
        output = [
            f"# Meeting Frequency Analysis ({len(meetings)} meetings)\n",
            "## Meetings by Month\n"
        ]
        
        for month, count in sorted(monthly_counts.items()):
            output.append(f"• **{month}:** {count} meetings")
        
        avg_per_month = len(meetings) / len(monthly_counts) if monthly_counts else 0
        output.append(f"\n**Average per month:** {avg_per_month:.1f}")
        
        return [TextContent(type="text", text="\n".join(output))]
    
    async def _analyze_topic_patterns(self, meetings: List[MeetingMetadata]) -> List[TextContent]:
        """Analyze topic patterns from meeting titles."""
        if not meetings:
            return [TextContent(type="text", text="No meetings found for analysis")]
        
        # Simple keyword extraction from titles
        word_counts = {}
        for meeting in meetings:
            words = meeting.title.lower().split()
            for word in words:
                # Filter out common words
                if len(word) > 3 and word not in ['meeting', 'call', 'sync', 'with']:
                    word_counts[word] = word_counts.get(word, 0) + 1
        
        if not word_counts:
            return [TextContent(type="text", text="No significant topics found in meeting titles")]
        
        sorted_topics = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
        
        output = [
            f"# Topic Analysis ({len(meetings)} meetings)\n",
            "## Most Common Topics (from titles)\n"
        ]
        
        for topic, count in sorted_topics[:15]:
            output.append(f"• **{topic}:** {count} mentions")
        
        return [TextContent(type="text", text="\n".join(output))]
    
    def run(self, transport_type: str = "stdio"):
        """Run the server."""
        import asyncio
        from mcp.server.stdio import stdio_server
        from mcp.types import ServerCapabilities
        
        if transport_type == "stdio":
            async def main():
                # Set up server capabilities for tool support
                capabilities = ServerCapabilities(
                    tools={}  # Empty dict indicates tool support is available
                )
                
                options = InitializationOptions(
                    server_name="granola-mcp-server",
                    server_version="0.1.0",
                    capabilities=capabilities
                )
                
                async with stdio_server() as (read_stream, write_stream):
                    await self.server.run(read_stream, write_stream, options)
            
            return asyncio.run(main())
        else:
            raise ValueError(f"Unsupported transport type: {transport_type}. Only 'stdio' is supported.")


def main():
    """Main entry point for the server."""
    import sys
    print("Starting Granola MCP Server...", file=sys.stderr)
    try:
        server = GranolaMCPServer()
        print(f"Initialized server, cache path: {server.cache_path}", file=sys.stderr)
        server.run()
    except Exception as e:
        print(f"Error starting server: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        raise
