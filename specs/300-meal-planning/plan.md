# Implementation Plan: Meal Planning Functionality

## Overview
This plan outlines the implementation of meal planning functionality for the Paprika MCP server, enabling users to add, remove, and list meals in their meal plan through MCP tools.

## Implementation Tasks

### 1. Research and Understand Paprika Meal Plan API
**Priority**: High  
**Estimated Time**: 2-4 hours

**Tasks**:
- Research Paprika API meal planning endpoints from the Go reference implementation: https://github.com/soggycactus/paprika-3-mcp/pull/3/files
- Identify the API endpoints for:
  - Getting meal plan entries
  - Adding meals to meal plan
  - Removing meals from meal plan
- Document API request/response formats
- Verify authentication requirements

**Deliverable**: API documentation notes with endpoint details

---

### 2. Implement Meal Plan Client Methods in `paprika_client.py`
**Priority**: High  
**Estimated Time**: 4-6 hours

**Tasks**:

#### 2.1 Add helper methods
- `_find_recipe_by_name(name: str, recipes: List[Dict]) -> Optional[Dict]`
  - Implement fuzzy matching using `difflib.SequenceMatcher` or `rapidfuzz`
  - Return recipe dict if match score is high (>85%)
  - Return list of potential matches if match score is medium (60-85%)
  - Return None if match score is poor (<60%)
- `_get_next_free_day(meal_plan: List[Dict], meal_type: str = "dinner") -> str`
  - Find next day without a meal of specified type
  - Return date in "YYYY-MM-DD" format

#### 2.2 Implement `get_meal_plan()` method
- **Signature**: `async def get_meal_plan(self, num_days: int = 10, meal_type: Optional[str] = "dinner") -> List[Dict[str, Any]]`
- **Functionality**:
  - Fetch meal plan from Paprika API
  - Filter by `num_days` (default 10)
  - Optionally filter by `meal_type` (default "dinner")
  - Parse and return list of meal plan entries
  - Handle API errors and propagate them literally (per non-functional requirements)

#### 2.3 Implement `add_meal_to_plan()` method
- **Signature**: `async def add_meal_to_plan(self, meal: Optional[str] = None, meal_id: Optional[str] = None, date: Optional[str] = None, meal_type: str = "dinner") -> Dict[str, Any]`
- **Functionality**:
  - If `meal_id` provided, use it directly
  - If `meal` provided:
    - Call `list_recipes()` to get all recipes
    - Use `_find_recipe_by_name()` to find matching recipe
    - Handle fuzzy matching results:
      - High match: proceed with recipe
      - Medium match: raise exception with suggestions
      - Poor match: raise exception offering to list all recipes
  - If `date` not provided, call `_get_next_free_day()` to find next available date
  - Make API call to add meal to plan
  - Return meal plan entry dict with: meal name, meal_ID, date, type
  - Handle API errors and propagate them literally

#### 2.4 Implement `remove_meal_from_plan()` method
- **Signature**: `async def remove_meal_from_plan(self, meal: Optional[str] = None, meal_id: Optional[str] = None, date: Optional[str] = None, meal_type: str = "dinner") -> Dict[str, Any]`
- **Functionality**:
  - Get current meal plan using `get_meal_plan()`
  - If no parameters provided, remove last meal from plan
  - If `meal` or `meal_id` provided:
    - Find matching meal in plan
    - If `date` provided, filter to that date
    - If multiple matches, use most recent or raise ambiguity error
  - If only `date` provided, remove meal from that date (with optional `meal_type` filter)
  - Make API call to remove meal
  - Return removed meal details: result, meal, meal_ID, date, type
  - Handle API errors and propagate them literally

**Deliverable**: Complete meal plan methods in `paprika_client.py` with proper error handling

---

### 3. Add Fuzzy Matching Dependency
**Priority**: Medium  
**Estimated Time**: 30 minutes

**Tasks**:
- Choose fuzzy matching library:
  - Option A: Use Python standard library `difflib` (no dependency)
  - Option B: Use `rapidfuzz` (faster, more accurate, requires dependency)
- Add dependency to `pyproject.toml` if using `rapidfuzz`
- Implement matching function with configurable thresholds

**Deliverable**: Fuzzy matching implementation in `paprika_client.py`

---

### 4. Implement MCP Tools in `server.py`
**Priority**: High  
**Estimated Time**: 3-4 hours

**Tasks**:

#### 4.1 Add `add_meal_to_plan` tool to `handle_list_tools()`
- Define tool schema with arguments:
  - `meal` (string, optional): Meal name for fuzzy matching
  - `meal_id` (string, optional): Recipe UUID
  - `date` (string, optional): Date in "YYYY-MM-DD" format
  - `type` (string, optional): Meal type, defaults to "dinner"
- Add tool description

#### 4.2 Add `remove_meal_from_plan` tool to `handle_list_tools()`
- Define tool schema with arguments:
  - `meal` (string, optional): Meal name
  - `meal_id` (string, optional): Recipe UUID
  - `date` (string, optional): Date in "YYYY-MM-DD" format
  - `type` (string, optional): Meal type, defaults to "dinner"
- Add tool description

#### 4.3 Add `list_meal_plan` tool to `handle_list_tools()`
- Define tool schema with arguments:
  - `num_days` (integer, optional): Number of days to show, defaults to 10
  - `meal_type` (string, optional): Filter by meal type, defaults to "dinner"
- Add tool description

#### 4.4 Implement tool handlers in `handle_call_tool()`
- Handle `add_meal_to_plan`:
  - Call `paprika_client.add_meal_to_plan()`
  - Format response: "Meal '{meal}' added to meal plan on {date}"
  - Handle fuzzy matching exceptions (medium/poor matches) and format suggestions
- Handle `remove_meal_from_plan`:
  - Call `paprika_client.remove_meal_from_plan()`
  - Format response: "Meal '{meal}' removed from meal plan on {date}"
- Handle `list_meal_plan`:
  - Call `paprika_client.get_meal_plan()`
  - Format response as list of meals with details
  - Handle empty meal plan case

**Deliverable**: Three new MCP tools fully implemented and registered

---

### 5. Error Handling Implementation
**Priority**: High  
**Estimated Time**: 2 hours

**Tasks**:
- Ensure all Paprika API errors are caught and returned literally in tool responses
- Implement custom exceptions:
  - `MealNotFoundError`: When meal matching fails
  - `MealPlanAmbiguityError`: When multiple matches found
- Format error messages appropriately for MCP responses
- Test error scenarios:
  - Invalid meal names
  - API errors
  - Network failures
  - Authentication failures

**Deliverable**: Robust error handling throughout meal planning functionality

---

### 6. Date Handling Utilities
**Priority**: Medium  
**Estimated Time**: 1-2 hours

**Tasks**:
- Implement date parsing and validation
- Handle date formats (YYYY-MM-DD)
- Calculate "next free day" logic:
  - Get current meal plan
  - Iterate from today forward
  - Find first day without meal of specified type
- Handle edge cases:
  - Empty meal plan
  - All days filled
  - Date in past (if applicable)

**Deliverable**: Date utility functions in `paprika_client.py`

---

### 7. Unit Tests
**Priority**: High  
**Estimated Time**: 4-6 hours

**Tasks**:
- Write tests for `_find_recipe_by_name()`:
  - High match scenario
  - Medium match scenario (test suggestions)
  - Poor match scenario
  - Exact match
  - Case insensitivity
- Write tests for `_get_next_free_day()`:
  - Empty meal plan
  - Partially filled plan
  - Fully filled plan
  - Different meal types
- Write tests for `get_meal_plan()`:
  - Default parameters
  - Custom num_days
  - Filter by meal_type
  - Empty meal plan
  - API error handling
- Write tests for `add_meal_to_plan()`:
  - Add by meal name (fuzzy match)
  - Add by meal_id
  - Add with explicit date
  - Add without date (auto-find next free day)
  - Error scenarios
- Write tests for `remove_meal_from_plan()`:
  - Remove by meal name
  - Remove by meal_id
  - Remove by date
  - Remove last meal (no params)
  - Ambiguity handling
- Mock Paprika API responses for all tests

**Deliverable**: Comprehensive test suite in `tests/test_meal_planning.py`

---

### 8. Integration Tests
**Priority**: Medium  
**Estimated Time**: 2-3 hours

**Tasks**:
- Test end-to-end workflows:
  - Add meal → List meal plan → Remove meal
  - Multiple meals on different dates
  - Fuzzy matching in real scenario
- Test MCP tool integration:
  - Tool registration
  - Tool argument parsing
  - Tool response formatting
- Test error propagation through MCP layer

**Deliverable**: Integration tests verifying full workflow

---

### 9. Documentation
**Priority**: Low  
**Estimated Time**: 1 hour

**Tasks**:
- Document new methods in `paprika_client.py` with docstrings
- Document MCP tools with descriptions
- Update README.md if needed
- Add usage examples

**Deliverable**: Complete code documentation

---

## Implementation Order

1. **Phase 1: Foundation** (Tasks 1, 3)
   - Research API
   - Set up fuzzy matching

2. **Phase 2: Core Functionality** (Tasks 2, 6)
   - Implement client methods
   - Implement date utilities

3. **Phase 3: MCP Integration** (Task 4)
   - Register and implement MCP tools

4. **Phase 4: Error Handling** (Task 5)
   - Implement robust error handling

5. **Phase 5: Testing** (Tasks 7, 8)
   - Write and run unit tests
   - Write and run integration tests

6. **Phase 6: Polish** (Task 9)
   - Documentation
   - Code review

---

## Dependencies

### External Libraries (if needed)
- `rapidfuzz` (optional, for better fuzzy matching) - requires `pyproject.toml` update

### Python Standard Library
- `difflib` (if not using rapidfuzz)
- `datetime` (for date handling)

---

## Risk Assessment

### High Risk
- **Paprika API changes**: The reference implementation is in Go, API might differ or be undocumented
  - **Mitigation**: Research thoroughly, test early with real API

### Medium Risk
- **Fuzzy matching accuracy**: Matching quality affects UX
  - **Mitigation**: Tune thresholds, test with various recipe names, consider user feedback

### Low Risk
- **Date handling edge cases**: Timezone issues, date format inconsistencies
  - **Mitigation**: Use standard ISO date format, document assumptions

---

## Success Criteria

1. All three MCP tools (`add_meal_to_plan`, `remove_meal_from_plan`, `list_meal_plan`) are functional
2. Fuzzy matching provides helpful suggestions for medium matches
3. Error messages from Paprika API are shown literally to users
4. All unit tests pass
5. Integration tests verify end-to-end workflows
6. Code follows existing patterns in `server.py` and `paprika_client.py`

---

## Future Considerations (Not in Scope)

- "Plan a week of meals" functionality (mentioned in specs as future expansion)
- Seasonal filtering (summer/winter recipes)
- Sorting by `last_prepared` date
- Multi-meal-type support beyond basic filtering

---

## Notes

- Follow existing code patterns in `server.py` for tool registration and handling
- Follow existing error handling patterns in `paprika_client.py`
- Maintain consistency with existing tool response formatting
- Reference the Go implementation for API endpoint details: https://github.com/soggycactus/paprika-3-mcp/pull/3/files

