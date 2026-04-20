# Python Engineer (ML) Challenge

## Background

Mimica automates repetitive computer-based tasks through human observation. Our software records clicks and keystrokes as users complete a task and outputs a step-by-step description of the task. Our web app allows users to edit the graph.

To do this, we make use of RAG (Retrieval Augmented Generation) to understand the context of what a user is doing at each step of their journey, and feed that context into other modelling approaches.

## What We Are Looking For

We are looking for excellent Python coders who are familiar and comfortable with Machine Learning. We hope this challenge will give you an opportunity to show your ability to write code that:

- Fulfils a set of requirements.
- Is intuitive and easy to maintain.
- Is easy to monitor and observe.
- Includes clear tests, and an understanding of why those components were tested.
- Demonstrates familiarity with async Python code.

## Challenge

Write a REST service in Python that enriches a Question Answering application with additional context about what a user was doing at the time.

The user will ask a question about a time period in a Project, e.g. "I am asking 'What license plates are visible?' in Project Cars between 10AM and 11AM today". This service will:

1. Retrieve the images for that time period for that project.
2. Identify which images are most relevant to the project.
3. Send those images to the QA endpoint of the Workflow Services API.
4. Return the answer to the user.

### Example Request Body

```json
{
  "project_id": "8b80353b-aee6-4835-ba7e-c3b79010bc0b",
  "from": 1754037000,
  "to": 1754039000,
  "question": "What car license plates are being looked at?"
}
```

### Required Flow

- The service must reach out to the Workflow Services API, which returns a stream of ND-JSON, to receive screenshot metadata for the given project.
- The service must retrieve the images relevant to the question from an S3-compatible bucket.
- Please mock the S3-compatible storage as an async-compatible service to simplify deployment. As long as it is async and separable from the main application, it is suitable.
- The service must filter images for relevance. This filtering is CPU-intensive. The implementation may be faked or mocked.
- Components must be swappable: replacing the S3 client or Workflow API client with a different implementation should require no changes to the core service.
- The service will send the most relevant images to the Workflow Services API `/qa/answer` endpoint, which returns the final answer.

The Workflow Services API specification for both the project data and the QA service is reproduced at the end of this document.

## Assumptions

- Many requests will be sent to this service in spiky patterns (5-10 per minute), arriving in burst patterns.
- Each request might require 50 to 500 images.
- A static URL will be available for the S3-compatible storage service (a cloud bucket). The bucket name is `mimica-screenshots`, and all Mimica applications can access it.
- A function exists that can filter images to those relevant to the question.

## Ambiguity

The following are intentionally left ambiguous:

- How should the response be formatted?
- How can testing be done without access to the services discussed?
- What kind of observability is appropriate?

## References

- Asyncio: https://docs.python.org/3/library/asyncio.html
- FastAPI: https://fastapi.tiangolo.com/
- Other REST frameworks are acceptable; FastAPI is always fine.
- Fake-GCS-Server: https://github.com/fsouza/fake-gcs-server
- Any other faking method is acceptable.

## Restrictions

- Do not use any paid external services. Do not spend money on this task.
- Evaluation is not based on whether the code would work with an LLM, only that the abstractions are suitable.
- Assume an observability system exists that is compatible with OpenTelemetry (OTEL).
- Do not assume any external applications other than the Workflow Services API.
- LLM use (Claude, OpenAI, etc.) during the challenge is expected and welcome.

## Requirements

Allocate a 3-hour timeframe to independently work on this challenge.

## Solution Format

Send a zipped folder containing:

- The source code of the solution.
- A README explaining how the solution has been tested against the criteria, and how others can test it.

Include the following notes in the submission email:

- How long was spent on the problem, approximately.
- What would have been done with more time.
- Feedback on the challenge.

## Workflow Services API Specification

```yaml
openapi: 3.0.3
info:
  title: Workflow Services API
  version: 1.0.0

servers:
  - url: https://api.example.com/v1

paths:
  /projects/{projectId}/stream:
    get:
      summary: Stream screenshot data for a project
      parameters:
        - name: projectId
          in: path
          required: true
          schema:
            type: string
            format: uuid
      responses:
        '200':
          description: Newline delimited stream of screenshots
          content:
            application/x-ndjson:
              schema:
                type: object
                properties:
                  timestamp:
                    type: integer
                    description: Unix timestamp
                    example: 1754037000
                  screenshot_url:
                    type: string
                    description: Image Identifier
                    example: "img123.png"
                required:
                  - timestamp
                  - screenshot_url
              example:
                value: |
                  {"timestamp": 1754037000, "screenshot_url": "img123.png"}
                  {"timestamp": 1754037005, "screenshot_url": "img124.png"}

  /qa/answer:
    post:
      summary: Answer questions based on screenshot embeddings
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                question:
                  type: string
                  example: "What car license plates are visible?"
                relevant_images:
                  type: array
                  items:
                    type: string
                  description: Image IDs
                  example:
                    - "img123.png"
                    - "img124.png"
              required:
                - question
                - relevant_images
      responses:
        '200':
          description: Answer to the question
          content:
            application/json:
              schema:
                type: object
                properties:
                  answer:
                    type: string
                    example: "The license plates visible are ABC-123 and XYZ-789"
                required:
                  - answer
```
